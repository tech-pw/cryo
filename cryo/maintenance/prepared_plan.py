"""Prepared-plan digest, expiry, and idempotency. No execution."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

DEFAULT_PLAN_TTL = timedelta(minutes=10)
CODE_IDEMPOTENCY_CONFLICT = "idempotency_conflict"
CODE_STALE_PLAN = "stale_plan"

_SECRET_PARTS = (
    "password",
    "passwd",
    "secret",
    "credential",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "session_token",
    "auth_token",
    "authorization",
    "webhook_url",
    "encrypted",
)
_SECRET_EXACT = frozenset({"token", "secret", "password", "authorization"})


@dataclass(frozen=True)
class Membership:
    table_ids: tuple[str, ...]


@dataclass(frozen=True)
class Recipe:
    destination_id: str | None = None
    preset_id: str | None = None
    revision: str | None = None
    mode: str | None = None
    operations: tuple[str, ...] = ()
    params: Mapping[str, object] = field(default_factory=dict)
    resource_plan_version: str | None = None


@dataclass(frozen=True)
class EvidenceVersion:
    table_id: str
    scan_version: str | None = None
    snapshot_version: str | None = None


@dataclass(frozen=True)
class TableDisposition:
    table_id: str
    blocked: bool
    active: bool = True


@dataclass(frozen=True)
class PlanDisposition:
    recipe: Recipe
    tables: tuple[TableDisposition, ...] = ()


@dataclass(frozen=True)
class ConflictPayload:
    code: str
    blocked_delta: int
    recipe_changed: bool
    deactivated: int


class PreparedPlanConflict(Exception):
    """A prepared plan was refused, with a typed diff payload."""

    def __init__(self, payload: ConflictPayload) -> None:
        self.payload = payload
        super().__init__(payload.code)


@dataclass(frozen=True)
class IdempotencyRecord:
    tenant_id: str
    principal: str
    key: str
    digest: str
    plan_id: str


def canonical_sha256(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def plan_material(
    membership: Membership | Sequence[str],
    recipe: Recipe,
    evidence: Sequence[EvidenceVersion] = (),
) -> dict[str, object]:
    versions = [_evidence_material(item) for item in evidence]
    versions.sort(key=_evidence_sort_key)
    return {
        "v": 1,
        "membership": list(_table_ids(membership)),
        "recipe": _recipe_material(recipe),
        "evidence": versions,
    }


def plan_digest(
    membership: Membership | Sequence[str],
    recipe: Recipe,
    evidence: Sequence[EvidenceVersion] = (),
) -> str:
    return canonical_sha256(plan_material(membership, recipe, evidence))


def expires_at(now: datetime | None = None, *, ttl: timedelta | None = None) -> datetime:
    moment = datetime.now(UTC) if now is None else now
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    else:
        moment = moment.astimezone(UTC)
    span = DEFAULT_PLAN_TTL if ttl is None else ttl
    if span.total_seconds() <= 0:
        raise ValueError("ttl must be positive")
    return moment + span


def lookup_idempotency(
    records: Sequence[IdempotencyRecord],
    *,
    tenant_id: str,
    principal: str,
    key: str,
    digest: str,
    reviewed: PlanDisposition | None = None,
    live: PlanDisposition | None = None,
) -> IdempotencyRecord | None:
    if (reviewed is None) != (live is None):
        raise ValueError("reviewed and live dispositions are required together")
    tenant = _required(tenant_id, "tenant_id")
    owner = _required(principal, "principal")
    token = _required(key, "key")
    bound = _required(digest, "digest")
    scoped = [
        record
        for record in records
        if _normalized(record.tenant_id) == tenant
        and _normalized(record.principal) == owner
        and _normalized(record.key) == token
    ]
    if not scoped:
        return None
    if any(record.digest != bound for record in scoped):
        payload = (
            summarize_conflict(reviewed, live, code=CODE_IDEMPOTENCY_CONFLICT)
            if reviewed is not None and live is not None
            else ConflictPayload(
                code=CODE_IDEMPOTENCY_CONFLICT,
                blocked_delta=0,
                recipe_changed=False,
                deactivated=0,
            )
        )
        raise PreparedPlanConflict(payload)
    return scoped[0]


def summarize_conflict(
    reviewed: PlanDisposition,
    live: PlanDisposition,
    *,
    code: str = CODE_STALE_PLAN,
) -> ConflictPayload:
    label = _required(code, "code")
    reviewed_tables = _by_id(reviewed.tables)
    live_tables = _by_id(live.tables)
    blocked_delta = _blocked_count(live_tables) - _blocked_count(reviewed_tables)
    deactivated = 0
    for table_id, prior in reviewed_tables.items():
        if not prior.active:
            continue
        current = live_tables.get(table_id)
        if current is None or not current.active:
            deactivated += 1
    return ConflictPayload(
        code=label,
        blocked_delta=blocked_delta,
        recipe_changed=_recipe_material(reviewed.recipe) != _recipe_material(live.recipe),
        deactivated=deactivated,
    )


def _table_ids(membership: Membership | Sequence[str]) -> tuple[str, ...]:
    if isinstance(membership, str):
        raise TypeError("membership must be table ids, not a string")
    raw: Sequence[object] = (
        membership.table_ids if isinstance(membership, Membership) else membership
    )
    found: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            found.append(text)
    return tuple(sorted(found))


def _recipe_material(recipe: Recipe) -> dict[str, object]:
    return {
        "destination_id": _text(recipe.destination_id),
        "preset_id": _text(recipe.preset_id),
        "revision": _text(recipe.revision),
        "mode": _text(recipe.mode),
        "operations": [str(item) for item in recipe.operations],
        "params": _jsonable(dict(recipe.params)),
        "resource_plan_version": _text(recipe.resource_plan_version),
    }


def _evidence_material(item: EvidenceVersion) -> dict[str, object]:
    return {
        "table_id": str(item.table_id).strip(),
        "scan_version": _text(item.scan_version),
        "snapshot_version": _text(item.snapshot_version),
    }


def _evidence_sort_key(item: Mapping[str, object]) -> tuple[str, str, str]:
    return (
        str(item.get("table_id") or ""),
        "" if item.get("scan_version") is None else str(item.get("scan_version")),
        "" if item.get("snapshot_version") is None else str(item.get("snapshot_version")),
    )


def _by_id(tables: Sequence[TableDisposition]) -> dict[str, TableDisposition]:
    found: dict[str, TableDisposition] = {}
    for table in tables:
        found[str(table.table_id).strip()] = table
    return found


def _blocked_count(tables: Mapping[str, TableDisposition]) -> int:
    return sum(1 for table in tables.values() if table.blocked)


def _text(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _required(value: object, name: str) -> str:
    text = _normalized(value)
    if not text:
        raise ValueError(f"{name} is required")
    return text


def _normalized(value: object) -> str:
    return str(value).strip()


def _is_secret_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    if normalized in _SECRET_EXACT:
        return True
    return any(part in normalized for part in _SECRET_PARTS)


def _jsonable(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return moment.astimezone(UTC).isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Mapping):
        cleaned: dict[str, object] = {}
        for key, item in value.items():
            name = str(key)
            if _is_secret_key(name):
                continue
            cleaned[name] = _jsonable(item)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return str(value)
