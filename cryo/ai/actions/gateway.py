"""Prepare, authorize, and dispatch Ask Cryo actions through existing write paths."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cryo.ai.actions.catalog import ActionSpec, get_action
from cryo.ai.actions.reconcile import reconcile_proposal
from cryo.api.security import Principal, Role
from cryo.db.models import (
    AssistantActionProposal,
    AssistantAuthorization,
    AssistantSettingsRow,
    AuditLog,
    MaintenanceRun,
    Preset,
    Table,
)
from cryo.maintenance.prepared_plan import canonical_sha256
from cryo.maintenance.prepared_plan import expires_at as plan_expires_at

_ACTIVE = frozenset(
    {
        "authorized",
        "queued",
        "running",
        "succeeded",
        "failed",
        "partial",
        "dispatch_uncertain",
    }
)
_CHANNELS = frozenset({"ui_card", "approval_link"})
_LAYOUT_EXECUTE = frozenset({"execute", "apply"})


class AuthorizationDenied(Exception):
    """The principal may not authorize this proposal."""


class Conflict(Exception):
    """The target or plan changed. A revised proposal was stored."""

    def __init__(self, revised_id: uuid.UUID) -> None:
        self.revised_id = revised_id
        super().__init__(str(revised_id))


class UnsupportedDispatch(Exception):
    """No existing write function can perform this action without a second run insert."""


class ActionGateway:
    def __init__(self, session: Session, *, origin: str = "assistant") -> None:
        self._session = session
        self._origin = origin if origin in {"assistant", "mcp"} else "assistant"

    def prepare(
        self,
        principal: Principal,
        action_type: str,
        args: dict[str, Any],
        conversation_id: uuid.UUID | None,
        message_id: uuid.UUID | None,
    ) -> AssistantActionProposal:
        spec = get_action(action_type)
        prepared = _json_ready(dict(args))
        if not isinstance(prepared, dict):
            raise ValueError("arguments must be an object")
        _require_notes(spec, prepared)
        key = str(prepared.get("idempotency_key") or uuid.uuid4())
        existing = self._session.scalar(
            select(AssistantActionProposal).where(AssistantActionProposal.idempotency_key == key)
        )
        if existing is not None:
            return existing
        digest = target_digest(self._session, prepared)
        plan_hash, precision = plan_fingerprint(self._session, spec.action_type, prepared)
        now = _now()
        proposal = AssistantActionProposal(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            message_id=message_id,
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            action_type=spec.action_type,
            tier=spec.tier,
            arguments=prepared,
            preview={"precision": precision},
            plan_hash=plan_hash,
            target_digest=digest,
            selection=_selection_blob(prepared),
            required_role=spec.min_role.value,
            status="awaiting_authorization",
            expires_at=plan_expires_at(
                now, ttl=timedelta(seconds=_settings().assistant_proposal_ttl_s)
            ),
            idempotency_key=key,
            created_at=now,
            updated_at=now,
        )
        self._session.add(proposal)
        try:
            self._session.commit()
        except IntegrityError:
            self._session.rollback()
            raced = self._session.scalar(
                select(AssistantActionProposal).where(
                    AssistantActionProposal.idempotency_key == key
                )
            )
            if raced is None:
                raise
            return raced
        self._session.refresh(proposal)
        return proposal

    def authorize(
        self,
        principal: Principal,
        proposal_id: uuid.UUID,
        channel: str,
        note: str | None,
        idempotency_key: str,
    ) -> AssistantActionProposal:
        proposal = self._require(proposal_id)
        if channel not in _CHANNELS or _confirm_set(proposal.arguments):
            raise AuthorizationDenied("a model boolean or chat message is not authorization")
        if not _tenant_ok(principal, proposal):
            raise AuthorizationDenied("tenant mismatch")
        if principal.subject != proposal.owner_subject and channel != "approval_link":
            raise AuthorizationDenied("only the owner can authorize this card")
        if proposal.run_id is not None:
            reconcile_proposal(self._session, proposal)
            self._session.refresh(proposal)
        if proposal.status in _ACTIVE:
            return proposal
        if proposal.status != "awaiting_authorization":
            raise AuthorizationDenied(f"proposal is {proposal.status}")
        if _as_utc(proposal.expires_at) <= _now():
            proposal.status = "expired"
            proposal.updated_at = _now()
            self._session.commit()
            raise AuthorizationDenied("proposal expired")
        required = Role(proposal.required_role)
        if not principal.can(required):
            raise AuthorizationDenied(f"requires {required.value}")
        if channel == "approval_link" and not principal.can(required):
            raise AuthorizationDenied(f"requires {required.value}")
        if proposal.action_type == "run_maintenance":
            _ensure_maintenance_writes()
        if proposal.tier >= 1 and not _settings().assistant_writes_enabled:
            raise AuthorizationDenied("assistant writes are disabled")
        if proposal.tier not in _enabled_tiers(self._session, proposal.tenant_id):
            raise AuthorizationDenied("action tier is not enabled")
        if proposal.action_type == "run_maintenance":
            _enforce_abac(self._session, principal, proposal.arguments)
        live_digest = target_digest(self._session, proposal.arguments)
        live_hash, precision = plan_fingerprint(
            self._session, proposal.action_type, proposal.arguments
        )
        if live_digest != proposal.target_digest or live_hash != proposal.plan_hash:
            revised = self._revise(proposal, live_digest, live_hash, precision)
            raise Conflict(revised.id)
        prior = self._session.scalar(
            select(AssistantAuthorization).where(
                AssistantAuthorization.correlation_id == idempotency_key
            )
        )
        if prior is not None:
            previous = self._session.get(AssistantActionProposal, prior.proposal_id)
            if previous is not None:
                return previous
        now = _now()
        self._session.add(
            AssistantAuthorization(
                id=uuid.uuid4(),
                proposal_id=proposal.id,
                principal_subject=principal.subject,
                channel=channel,
                note=note,
                correlation_id=idempotency_key,
                authorized_at=now,
            )
        )
        self._session.add(
            AuditLog(
                action=f"assistant.{proposal.action_type}",
                status="authorized",
                actor=principal.subject,
                origin=self._origin,
                proposal_id=proposal.id,
                detail=note,
                table_id=_first_table_id(proposal.arguments),
            )
        )
        proposal.status = "authorized"
        proposal.updated_at = now
        self._session.commit()
        try:
            result = _dispatch(self._session, principal, proposal)
        except UnsupportedDispatch as exc:
            proposal.status = "dispatch_uncertain"
            proposal.last_error = str(exc)
            proposal.updated_at = _now()
            self._session.commit()
            return proposal
        except Exception as exc:
            self._mark_dispatch_failure(proposal.id, exc)
            refreshed = self._require(proposal.id)
            return refreshed
        if result.run_id is not None:
            proposal.run_id = result.run_id
            reconcile_proposal(self._session, proposal)
        else:
            proposal.status = "succeeded"
            proposal.updated_at = _now()
            self._session.commit()
        self._session.refresh(proposal)
        return proposal

    def status(self, principal: Principal, proposal_id: uuid.UUID) -> AssistantActionProposal:
        proposal = self._require(proposal_id)
        if not _tenant_ok(principal, proposal) and principal.subject != proposal.owner_subject:
            raise AuthorizationDenied("proposal not visible")
        if proposal.run_id is not None:
            reconcile_proposal(self._session, proposal)
            self._session.refresh(proposal)
        return proposal

    def reject(self, principal: Principal, proposal_id: uuid.UUID) -> AssistantActionProposal:
        proposal = self._require(proposal_id)
        if not _tenant_ok(principal, proposal):
            raise AuthorizationDenied("tenant mismatch")
        if principal.subject != proposal.owner_subject and not principal.can(
            Role(proposal.required_role)
        ):
            raise AuthorizationDenied("not allowed to reject this proposal")
        if proposal.status in _ACTIVE:
            return proposal
        proposal.status = "rejected"
        proposal.updated_at = _now()
        self._session.commit()
        return proposal

    def _require(self, proposal_id: uuid.UUID) -> AssistantActionProposal:
        proposal = self._session.get(AssistantActionProposal, proposal_id)
        if proposal is None:
            raise AuthorizationDenied("proposal not found")
        return proposal

    def _revise(
        self,
        proposal: AssistantActionProposal,
        digest: str,
        plan_hash: str,
        precision: str,
    ) -> AssistantActionProposal:
        now = _now()
        revised = AssistantActionProposal(
            id=uuid.uuid4(),
            conversation_id=proposal.conversation_id,
            message_id=proposal.message_id,
            tenant_id=proposal.tenant_id,
            owner_subject=proposal.owner_subject,
            action_type=proposal.action_type,
            tier=proposal.tier,
            arguments=dict(proposal.arguments),
            preview={"precision": precision},
            plan_hash=plan_hash,
            target_digest=digest,
            selection=proposal.selection,
            required_role=proposal.required_role,
            status="awaiting_authorization",
            expires_at=plan_expires_at(
                now, ttl=timedelta(seconds=_settings().assistant_proposal_ttl_s)
            ),
            idempotency_key=f"{proposal.idempotency_key}:revised:{uuid.uuid4()}",
            created_at=now,
            updated_at=now,
        )
        self._session.add(revised)
        self._session.flush()
        proposal.status = "superseded"
        proposal.superseded_by = revised.id
        proposal.updated_at = now
        self._session.commit()
        return revised

    def _mark_dispatch_failure(self, proposal_id: uuid.UUID, exc: BaseException) -> None:
        self._session.rollback()
        proposal = self._session.get(AssistantActionProposal, proposal_id)
        if proposal is None:
            return
        run = self._session.scalar(
            select(MaintenanceRun).where(MaintenanceRun.trigger == f"assistant:{proposal_id}")
        )
        if run is not None:
            proposal.run_id = run.id
        uncertain = run is not None or _after_possible_commit(exc)
        proposal.status = "dispatch_uncertain" if uncertain else "failed"
        proposal.last_error = _exc_text(exc)
        proposal.updated_at = _now()
        self._session.commit()


def target_digest(session: Session, args: dict[str, Any]) -> str:
    table_ids = sorted(_table_id_strings(args))
    live: list[dict[str, object]] = []
    for raw in table_ids:
        table = _table(session, raw)
        if table is None:
            live.append({"id": raw, "state": "missing"})
        else:
            live.append(
                {
                    "id": raw,
                    "ref": f"{table.schema_name}.{table.table_name}",
                    "active": bool(table.is_active),
                }
            )
    payload = {
        "filter": _filter_spec(args),
        "live": live,
        "snapshot_id": _snapshot_token(args.get("snapshot_id")),
        "table_ids": table_ids,
    }
    return _sha256(payload)


def plan_fingerprint(session: Session, action_type: str, args: dict[str, Any]) -> tuple[str, str]:
    material: dict[str, object] = {"action_type": action_type, "args": _stable_args(args)}
    precision = "arguments"
    preset_id = args.get("preset_id")
    if action_type == "run_maintenance" and preset_id:
        preset = _table_like_preset(session, preset_id)
        if preset is not None:
            material["preset_operations"] = list(preset.operations or [])
            material["preset_params"] = preset.params or {}
            precision = "preset_arguments"
    return _sha256(material), precision


def _dispatch(
    session: Session,
    principal: Principal,
    proposal: AssistantActionProposal,
) -> _DispatchResult:
    args = dict(proposal.arguments)
    action = proposal.action_type
    if action == "run_maintenance":
        return _dispatch_maintenance(session, principal, proposal.id, args)
    if action == "scan_table":
        return _dispatch_scan(session, principal, args)
    if action == "save_view":
        return _dispatch_save_view(session, principal, args)
    if action == "subscribe_alerts":
        return _dispatch_subscribe(session, principal, args)
    if action == "protect_snapshot":
        return _dispatch_protect(session, args, protect=True)
    if action == "unprotect_snapshot":
        return _dispatch_protect(session, args, protect=False)
    if action == "dismiss_unused_table":
        return _dispatch_dismiss(session, principal, args)
    if action == "act_on_recommendation":
        return _dispatch_recommendation(session, principal, args)
    if action == "act_on_approval":
        return _dispatch_approval(session, principal, args)
    if action in {"pause_maintenance", "resume_maintenance"}:
        return _dispatch_pause(session, principal, args, paused=action == "pause_maintenance")
    if action == "cancel_run":
        return _dispatch_cancel(session, args)
    if action == "set_table_override":
        return _dispatch_override(session, args)
    if action == "update_preset":
        return _dispatch_preset(session, args)
    raise UnsupportedDispatch(f"no dispatcher for {action}")


class _DispatchResult:
    def __init__(self, run_id: uuid.UUID | None = None) -> None:
        self.run_id = run_id


def _dispatch_maintenance(
    session: Session,
    principal: Principal,
    proposal_id: uuid.UUID,
    args: dict[str, Any],
) -> _DispatchResult:
    from cryo.api.schemas import AssignmentSelection, MaintenanceRunRequest
    from cryo.domain.enums import Mode
    from cryo.maintenance.run_service import start_maintenance_run

    selection = None
    if isinstance(args.get("selection"), dict):
        selection = AssignmentSelection.model_validate(args["selection"])
    operations = args.get("operations")
    payload = MaintenanceRunRequest(
        table_ids=_uuid_list(args.get("table_ids")),
        selection=selection,
        destination_id=uuid.UUID(str(args["destination_id"])),
        preset_id=uuid.UUID(str(args["preset_id"])),
        mode=Mode(str(args.get("mode") or "standard")),
        trigger=f"assistant:{proposal_id}",
        operations=list(operations) if isinstance(operations, list) else None,
    )
    result = start_maintenance_run(session, payload, principal)
    return _DispatchResult(run_id=result.run_id)


def _dispatch_scan(session: Session, principal: Principal, args: dict[str, Any]) -> _DispatchResult:
    from cryo.db.repositories.datalake import create_scan_trigger

    table_ids = _uuid_list(args.get("table_ids"))
    if not table_ids and args.get("table_id"):
        table_ids = _uuid_list([args["table_id"]])
    created = create_scan_trigger(
        session,
        {
            "scope": args.get("scope") or ("tables" if table_ids else "all"),
            "source_id": _maybe_uuid(args.get("source_id")),
            "table_ids": table_ids,
            "table_names": list(args.get("table_names") or []),
            "schema_names": list(args.get("schema_names") or []),
            "trigger": "assistant",
            "requested_by": principal.subject,
            "full_scan": bool(args.get("full_scan", True)),
            "cost_full_scan": bool(args.get("cost_full_scan", False)),
        },
    )
    run_id = created.get("run_id")
    return _DispatchResult(run_id=run_id if isinstance(run_id, uuid.UUID) else None)


def _dispatch_save_view(
    session: Session, principal: Principal, args: dict[str, Any]
) -> _DispatchResult:
    from cryo.api.routes_explorer_views import ExplorerViewWrite, save_explorer_view

    save_explorer_view(
        ExplorerViewWrite(
            name=str(args["name"]),
            filters=dict(args.get("filters") or {}),
            sort=args.get("sort") if isinstance(args.get("sort"), str) else None,
            group_by=args.get("group_by") if isinstance(args.get("group_by"), str) else None,
            savings_view=bool(args.get("savings_view", False)),
        ),
        session,
        principal,
    )
    return _DispatchResult()


def _dispatch_subscribe(
    session: Session, principal: Principal, args: dict[str, Any]
) -> _DispatchResult:
    from cryo.api.routes import create_table_alert_subscription
    from cryo.api.schemas import TableAlertSubscriptionCreate

    create_table_alert_subscription(
        uuid.UUID(str(args["table_id"])),
        TableAlertSubscriptionCreate(email=str(args["email"])),
        session,
        principal,
    )
    return _DispatchResult()


def _dispatch_protect(session: Session, args: dict[str, Any], *, protect: bool) -> _DispatchResult:
    from cryo.db.repositories.protected_snapshots import ProtectedSnapshotRepository

    table_id = uuid.UUID(str(args["table_id"]))
    snapshot_id = _parse_snapshot_id(args.get("snapshot_id"))
    repo = ProtectedSnapshotRepository(session)
    if protect:
        label = args.get("label")
        repo.protect(
            table_id=table_id,
            snapshot_id=snapshot_id,
            label=str(label) if label is not None else None,
        )
    else:
        repo.unprotect(table_id=table_id, snapshot_id=snapshot_id)
    return _DispatchResult()


def _dispatch_dismiss(
    session: Session, principal: Principal, args: dict[str, Any]
) -> _DispatchResult:
    from cryo.api.routes_dark_tables import dismiss_dark_table
    from cryo.api.schemas import DarkTableDismissRequest

    dismiss_dark_table(
        uuid.UUID(str(args["table_id"])),
        DarkTableDismissRequest(reason=str(args["reason"])),
        session,
        principal,
    )
    return _DispatchResult()


def _dispatch_recommendation(
    session: Session, principal: Principal, args: dict[str, Any]
) -> _DispatchResult:
    decision = str(args.get("decision") or "approve")
    if decision in _LAYOUT_EXECUTE:
        raise UnsupportedDispatch(
            "layout execute inserts MaintenanceRun outside start_maintenance_run"
        )
    recommendation_id = uuid.UUID(str(args["recommendation_id"]))
    if decision in {"dismiss", "reject"}:
        from cryo.api.routes_smart_maintenance import (
            DismissLayoutRecommendationRequest,
            dismiss_layout_recommendation,
        )

        dismiss_layout_recommendation(
            recommendation_id,
            DismissLayoutRecommendationRequest(reason=str(args.get("reason") or "dismissed")),
            session,
            principal,
        )
        return _DispatchResult()
    from cryo.api.routes_smart_maintenance import (
        ApproveLayoutRecommendationRequest,
        approve_layout_recommendation,
    )

    note = args.get("note")
    approve_layout_recommendation(
        recommendation_id,
        ApproveLayoutRecommendationRequest(note=str(note) if note is not None else None),
        session,
        principal,
    )
    return _DispatchResult()


def _dispatch_approval(
    session: Session, principal: Principal, args: dict[str, Any]
) -> _DispatchResult:
    from cryo.workflows.approvals import approve_request, reject_request

    request_id = uuid.UUID(str(args["request_id"]))
    decision = str(args.get("decision") or "approve")
    reason = str(args["note"]) if args.get("note") else None
    if decision in {"reject", "rejected"}:
        reject_request(session, request_id, principal.subject, reason)
    else:
        approve_request(session, request_id, principal.subject, reason)
    session.commit()
    return _DispatchResult()


def _dispatch_pause(
    session: Session,
    principal: Principal,
    args: dict[str, Any],
    *,
    paused: bool,
) -> _DispatchResult:
    from cryo.db.repositories.intents import set_pause_state

    scope_type = str(args.get("scope_type") or "global")
    scope_id = str(args.get("scope_id") or "global")
    set_pause_state(
        session,
        scope_type=scope_type,
        scope_id=scope_id,
        paused=paused,
        changed_by=principal.subject,
        reason=str(args.get("note") or ""),
    )
    session.commit()
    return _DispatchResult()


def _dispatch_cancel(session: Session, args: dict[str, Any]) -> _DispatchResult:
    from cryo.api.routes import cancel_run

    run_id = uuid.UUID(str(args["run_id"]))
    cancel_run(run_id, session)
    return _DispatchResult(run_id=run_id)


def _dispatch_override(session: Session, args: dict[str, Any]) -> _DispatchResult:
    from cryo.api.routes import patch_table_param_overrides
    from cryo.api.schemas import TableParamOverridePatch

    overrides = args.get("param_overrides")
    if not isinstance(overrides, dict):
        raise ValueError("param_overrides must be an object")
    patch_table_param_overrides(
        uuid.UUID(str(args["table_id"])),
        TableParamOverridePatch(param_overrides=overrides),
        session,
    )
    return _DispatchResult()


def _dispatch_preset(session: Session, args: dict[str, Any]) -> _DispatchResult:
    from cryo.api.routes import patch_preset
    from cryo.api.schemas import PresetPatch

    fields = {
        key: args[key]
        for key in (
            "name",
            "destination_id",
            "description",
            "mode",
            "operations",
            "params",
            "schedule_cron",
            "priority",
            "enabled",
            "guardrails",
            "is_default",
        )
        if key in args
    }
    patch_preset(uuid.UUID(str(args["preset_id"])), PresetPatch.model_validate(fields), session)
    return _DispatchResult()


def _require_notes(spec: ActionSpec, args: dict[str, Any]) -> None:
    if spec.requires_note and not str(args.get("note") or "").strip():
        raise ValueError("note is required")
    if spec.requires_reason and not str(args.get("reason") or "").strip():
        raise ValueError("reason is required")


def _confirm_set(args: dict[str, Any]) -> bool:
    value = args.get("confirm")
    return value is True or value in {"true", "True", "1", 1}


def _enabled_tiers(session: Session, tenant_id: uuid.UUID | None) -> set[int]:
    if tenant_id is None:
        return {0}
    row = session.get(AssistantSettingsRow, tenant_id)
    if row is None or not row.enabled_tiers:
        return {0}
    tiers: set[int] = set()
    raw_tiers = cast("list[object]", list(row.enabled_tiers))
    for item in raw_tiers:
        if isinstance(item, bool) or not isinstance(item, (int, str)):
            continue
        try:
            tiers.add(int(item))
        except ValueError:
            continue
    return tiers or {0}


def _enforce_abac(session: Session, principal: Principal, args: dict[str, Any]) -> None:
    from cryo.domain.enums import OperationType
    from cryo.security.abac_enforcement import evaluate_maintenance_allowed

    table_ids = _uuid_list(args.get("table_ids"))
    if not table_ids and args.get("table_id"):
        table_ids = _uuid_list([args["table_id"]])
    operations = args.get("operations")
    op_names = [str(item) for item in operations] if isinstance(operations, list) else ["optimize"]
    for table_id in table_ids:
        for op_name in op_names:
            try:
                operation: OperationType | str = OperationType(op_name)
            except ValueError:
                operation = op_name
            allowed, reason = evaluate_maintenance_allowed(session, table_id, principal, operation)
            if not allowed:
                raise AuthorizationDenied(reason or "ABAC denied")


def _ensure_maintenance_writes() -> None:
    from cryo.maintenance.writes import MaintenanceWritesDisabled, ensure_maintenance_writes_enabled

    try:
        ensure_maintenance_writes_enabled()
    except MaintenanceWritesDisabled as exc:
        raise AuthorizationDenied(str(exc)) from exc


def _tenant_ok(principal: Principal, proposal: AssistantActionProposal) -> bool:
    if proposal.tenant_id is None:
        return True
    return principal.tenant_id == proposal.tenant_id


def _after_possible_commit(exc: BaseException) -> bool:
    from fastapi import HTTPException

    if isinstance(exc, UnsupportedDispatch):
        return True
    if isinstance(exc, HTTPException) and exc.status_code < 500:
        return False
    return not isinstance(exc, (ValueError, AuthorizationDenied, Conflict, KeyError, LookupError))


def _exc_text(exc: BaseException) -> str:
    from fastapi import HTTPException

    if isinstance(exc, HTTPException):
        return str(exc.detail)[:500]
    text = str(exc).strip() or type(exc).__name__
    return text[:500]


def _settings() -> Any:
    from cryo.config import get_settings

    return get_settings()


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _sha256(payload: object) -> str:
    return canonical_sha256(payload)


def _stable_args(args: dict[str, Any]) -> dict[str, Any]:
    skipped = {"confirm", "idempotency_key"}
    return {key: value for key, value in args.items() if key not in skipped}


def _filter_spec(args: dict[str, Any]) -> object:
    if "filter" in args:
        return args.get("filter")
    selection = args.get("selection")
    if isinstance(selection, dict):
        return selection.get("filter")
    return None


def _selection_blob(args: dict[str, Any]) -> dict[str, Any] | None:
    selection = args.get("selection")
    if isinstance(selection, dict):
        return selection
    table_ids = _table_id_strings(args)
    if not table_ids:
        return None
    return {"mode": "ids", "ids": table_ids}


def _table_id_strings(args: dict[str, Any]) -> list[str]:
    raw = args.get("table_ids")
    values: list[object] = list(raw) if isinstance(raw, list) else []
    if args.get("table_id"):
        values.append(args["table_id"])
    found: list[str] = []
    for item in values:
        text = str(item)
        if text and text not in found:
            found.append(text)
    return found


def _uuid_list(raw: object) -> list[uuid.UUID]:
    if not isinstance(raw, list):
        return []
    return [uuid.UUID(str(item)) for item in raw]


def _maybe_uuid(raw: object) -> uuid.UUID | None:
    if raw in (None, ""):
        return None
    return uuid.UUID(str(raw))


def _first_table_id(args: dict[str, Any]) -> uuid.UUID | None:
    ids = _table_id_strings(args)
    if not ids:
        return None
    try:
        return uuid.UUID(ids[0])
    except ValueError:
        return None


def _table(session: Session, raw: str) -> Table | None:
    try:
        return session.get(Table, uuid.UUID(raw))
    except ValueError:
        return None


def _table_like_preset(session: Session, raw: object) -> Preset | None:
    try:
        return session.get(Preset, uuid.UUID(str(raw)))
    except ValueError:
        return None


def _snapshot_token(raw: object) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, float):
        return None
    return str(raw)


def _parse_snapshot_id(raw: object) -> int:
    if isinstance(raw, bool) or isinstance(raw, float):
        raise ValueError("snapshot_id must be a string or int")
    if isinstance(raw, int):
        return raw
    text = str(raw).strip()
    if not text.lstrip("-").isdigit():
        raise ValueError("snapshot_id must be an integer string")
    return int(text)


def _json_ready(value: object) -> object:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value
