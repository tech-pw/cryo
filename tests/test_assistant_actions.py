from __future__ import annotations

import hashlib
import hmac
import inspect
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.ai.actions.catalog import CATALOG, get_action
from cryo.ai.actions.gateway import ActionGateway, AuthorizationDenied, Conflict
from cryo.ai.actions.reconcile import reconcile_proposal
from cryo.api.schemas import MaintenanceRunRequest
from cryo.api.security import Principal, Role
from cryo.api.slack_routes import slack_interaction_response
from cryo.config import get_settings
from cryo.db.models import (
    AssistantActionProposal,
    AssistantSettingsRow,
    Destination,
    MaintenanceJob,
    MaintenanceRun,
    Preset,
    Source,
    Table,
)
from cryo.db.repositories.job_store import _job_record
from cryo.domain.enums import OperationType
from cryo.maintenance.run_service import start_maintenance_run
from cryo.mcp.slack_verify import verify_slack_signature

_SLACK_SECRET = "8f742231b10e8888abcd99yyyzzz85a5"
_SLACK_TS = "1710000000"
_SLACK_BODY = b"token=test&text=diagnose"
_SLACK_SIG = "v0=3037bd0b8fef82941bad5ace1ddbb8191674ff8eac115094ac0c692a6c9d2bc9"


class _Queue:
    def enqueue(self, *_args: object, **_kwargs: object) -> None:
        return None


class _Events:
    def publish(self, *_args: object, **_kwargs: object) -> None:
        return None


class _Wiring:
    queue = _Queue()
    run_events = _Events()


def _flags(monkeypatch: pytest.MonkeyPatch, **updates: object) -> None:
    settings = get_settings().model_copy(update=updates)
    monkeypatch.setattr("cryo.config.get_settings", lambda: settings)
    monkeypatch.setattr("cryo.api.routes.get_settings", lambda: settings)
    monkeypatch.setattr("cryo.api.routes.build_runtime_wiring", lambda: _Wiring())


def _principal(role: Role, tenant_id: uuid.UUID, subject: str = "operator") -> Principal:
    return Principal(subject=subject, roles=frozenset({role}), tenant_id=tenant_id)


def _enable_tiers(session: Session, tenant_id: uuid.UUID) -> None:
    session.add(
        AssistantSettingsRow(
            tenant_id=tenant_id,
            enabled=True,
            allowed_min_role="viewer",
            enabled_tiers=[0, 1, 2],
            send_row_values=False,
            retention_days=90,
            updated_at=datetime.now(UTC),
        )
    )
    session.commit()


def _seed(session: Session) -> dict[str, uuid.UUID]:
    source_id = uuid.uuid4()
    destination_id = uuid.uuid4()
    table_id = uuid.uuid4()
    preset_id = uuid.uuid4()
    session.add(Source(id=source_id, name=f"src-{source_id}", type="rest", config={}))
    session.add(
        Destination(id=destination_id, name=f"dest-{destination_id}", engine="trino", config={})
    )
    session.add(
        Table(
            id=table_id,
            source_id=source_id,
            schema_name="analytics",
            table_name="events",
            is_active=True,
        )
    )
    session.add(
        Preset(
            id=preset_id,
            name=f"preset-{preset_id}",
            destination_id=destination_id,
            mode="standard",
            operations=["optimize", "expire_snapshots"],
            params={},
            guardrails={},
            enabled=True,
        )
    )
    session.commit()
    return {
        "table_id": table_id,
        "destination_id": destination_id,
        "preset_id": preset_id,
    }


def _args(seed: dict[str, uuid.UUID]) -> dict[str, object]:
    return {
        "table_ids": [str(seed["table_id"])],
        "destination_id": str(seed["destination_id"]),
        "preset_id": str(seed["preset_id"]),
        "mode": "standard",
    }


def _runs(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(MaintenanceRun)) or 0)


def _arm(monkeypatch: pytest.MonkeyPatch, *, writes: bool = True) -> None:
    _flags(
        monkeypatch,
        assistant_enabled=True,
        assistant_writes_enabled=writes,
        maintenance_writes_enabled=True,
        assistant_proposal_ttl_s=600,
        plan_verifier_enabled=False,
        durable_intents_enabled=False,
    )


def test_forged_confirm_does_not_create_a_run(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _arm(monkeypatch)
    tenant_id = uuid.uuid4()
    _enable_tiers(session, tenant_id)
    seed = _seed(session)
    principal = _principal(Role.OPERATOR, tenant_id)
    gateway = ActionGateway(session)
    proposal = gateway.prepare(
        principal,
        "run_maintenance",
        {**_args(seed), "confirm": True},
        None,
        None,
    )
    with pytest.raises(AuthorizationDenied):
        gateway.authorize(principal, proposal.id, "ui_card", None, "confirm-1")
    assert _runs(session) == 0


def test_expired_proposal_cannot_authorize(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _arm(monkeypatch)
    tenant_id = uuid.uuid4()
    _enable_tiers(session, tenant_id)
    seed = _seed(session)
    principal = _principal(Role.OPERATOR, tenant_id)
    gateway = ActionGateway(session)
    proposal = gateway.prepare(principal, "run_maintenance", _args(seed), None, None)
    proposal.expires_at = datetime.now(UTC) - timedelta(seconds=5)
    session.commit()
    with pytest.raises(AuthorizationDenied):
        gateway.authorize(principal, proposal.id, "ui_card", None, "expired-1")
    session.refresh(proposal)
    assert proposal.status == "expired"
    assert _runs(session) == 0


def test_viewer_cannot_authorize_run_maintenance(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _arm(monkeypatch)
    tenant_id = uuid.uuid4()
    _enable_tiers(session, tenant_id)
    seed = _seed(session)
    owner = _principal(Role.OPERATOR, tenant_id, subject="owner")
    viewer = _principal(Role.VIEWER, tenant_id, subject="owner")
    gateway = ActionGateway(session)
    proposal = gateway.prepare(owner, "run_maintenance", _args(seed), None, None)
    with pytest.raises(AuthorizationDenied):
        gateway.authorize(viewer, proposal.id, "ui_card", None, "viewer-1")
    assert _runs(session) == 0


def test_assistant_writes_disabled_blocks_authorize(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _arm(monkeypatch, writes=False)
    tenant_id = uuid.uuid4()
    _enable_tiers(session, tenant_id)
    seed = _seed(session)
    principal = _principal(Role.OPERATOR, tenant_id)
    gateway = ActionGateway(session)
    proposal = gateway.prepare(principal, "run_maintenance", _args(seed), None, None)
    with pytest.raises(AuthorizationDenied):
        gateway.authorize(principal, proposal.id, "ui_card", None, "writes-1")
    assert _runs(session) == 0


def test_plan_change_supersedes_and_conflicts(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _arm(monkeypatch)
    tenant_id = uuid.uuid4()
    _enable_tiers(session, tenant_id)
    seed = _seed(session)
    principal = _principal(Role.OPERATOR, tenant_id)
    gateway = ActionGateway(session)
    proposal = gateway.prepare(principal, "run_maintenance", _args(seed), None, None)
    preset = session.get(Preset, seed["preset_id"])
    assert preset is not None
    preset.operations = ["expire_snapshots"]
    table = session.get(Table, seed["table_id"])
    assert table is not None
    table.table_name = "events_renamed"
    session.commit()
    with pytest.raises(Conflict) as caught:
        gateway.authorize(principal, proposal.id, "ui_card", None, "conflict-1")
    session.refresh(proposal)
    revised = session.get(AssistantActionProposal, caught.value.revised_id)
    assert proposal.status == "superseded"
    assert proposal.superseded_by == caught.value.revised_id
    assert revised is not None
    assert revised.status == "awaiting_authorization"
    assert _runs(session) == 0


def test_double_authorize_does_not_create_two_runs(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _arm(monkeypatch)
    tenant_id = uuid.uuid4()
    _enable_tiers(session, tenant_id)
    seed = _seed(session)
    principal = _principal(Role.OPERATOR, tenant_id)
    gateway = ActionGateway(session)
    proposal = gateway.prepare(principal, "run_maintenance", _args(seed), None, None)
    first = gateway.authorize(principal, proposal.id, "ui_card", "ship it", "once")
    second = gateway.authorize(principal, proposal.id, "ui_card", "ship it", "once")
    assert first.id == second.id
    assert _runs(session) == 1
    assert first.run_id is not None


def test_tier3_names_are_absent(session: Session) -> None:
    for name in ("purge", "force", "deactivate", "restore", "force-admit", "force_admit"):
        assert name not in CATALOG
        with pytest.raises(ValueError):
            get_action(name)
    gateway = ActionGateway(session)
    principal = _principal(Role.ADMIN, uuid.uuid4())
    with pytest.raises(ValueError):
        gateway.prepare(principal, "purge", {}, None, None)
    assert session.scalar(select(func.count()).select_from(AssistantActionProposal)) == 0


def test_slack_diagnose_returns_a_run_link_and_does_not_describe_a_model() -> None:
    run_id = "00000000-0000-0000-0000-0000000000ab"
    response = slack_interaction_response({"actions": [{"action_id": "diagnose", "value": run_id}]})
    assert response["text"] == f"Open this run in Cryo: /runs/{run_id}"
    blocked = slack_interaction_response(
        {"actions": [{"action_id": "run_maintenance", "value": "confirm"}]}
    )
    assert "does not run maintenance" in blocked["text"]


def test_slack_signature_known_vector_and_stale_timestamp() -> None:
    issued = datetime.fromtimestamp(int(_SLACK_TS), UTC)
    assert verify_slack_signature(_SLACK_BODY, _SLACK_TS, _SLACK_SIG, _SLACK_SECRET, now=issued)
    stale_body = b"token=stale"
    stale_ts = "1"
    stale_sig = (
        "v0="
        + hmac.new(
            _SLACK_SECRET.encode(),
            b"v0:" + stale_ts.encode() + b":" + stale_body,
            hashlib.sha256,
        ).hexdigest()
    )
    assert verify_slack_signature(stale_body, stale_ts, stale_sig, _SLACK_SECRET) is False


def test_operations_subset_is_shared_with_the_run_service(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _arm(monkeypatch)
    seed = _seed(session)
    principal = _principal(Role.OPERATOR, uuid.uuid4())
    with pytest.raises(HTTPException) as rejected:
        start_maintenance_run(
            session,
            MaintenanceRunRequest(
                table_ids=[seed["table_id"]],
                destination_id=seed["destination_id"],
                preset_id=seed["preset_id"],
                operations=["optimize", "compute_statistics"],
            ),
            principal,
        )
    assert rejected.value.status_code == 400
    assert _runs(session) == 0
    created = start_maintenance_run(
        session,
        MaintenanceRunRequest(
            table_ids=[seed["table_id"]],
            destination_id=seed["destination_id"],
            preset_id=seed["preset_id"],
            operations=["expire_snapshots", "optimize"],
        ),
        principal,
    )
    run = session.get(MaintenanceRun, created.run_id)
    assert run is not None
    assert run.summary is not None
    assert run.summary["operations"] == ["optimize", "expire_snapshots"]
    job = session.scalar(select(MaintenanceJob).where(MaintenanceJob.run_id == run.id))
    assert job is not None
    record = _job_record(session, job)
    assert record.operations == (OperationType.OPTIMIZE, OperationType.EXPIRE_SNAPSHOTS)


def test_reconcile_has_no_caller_status() -> None:
    assert "status" not in inspect.signature(reconcile_proposal).parameters


def test_pause_without_note_fails_prepare(session: Session) -> None:
    gateway = ActionGateway(session)
    principal = _principal(Role.OPERATOR, uuid.uuid4())
    with pytest.raises(ValueError):
        gateway.prepare(principal, "pause_maintenance", {"scope_type": "global"}, None, None)
