"""Process-local selection receipts. No engine, scan, or enqueue."""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from cryo.api.predicates import compile_predicate
from cryo.api.table_view_filters import TableViewFilter, build_table_view_filter
from cryo.db.model_utils import catalog_name_for_source
from cryo.db.models import Source, Table, TableAssignment, TableCost, TableStats
from cryo.db.tenant_scope import scope_by_tenant

DEFAULT_BATCH_CAP = 100
DEFAULT_RECEIPT_TTL = timedelta(minutes=10)
_PREDICATE_KEYS = frozenset({"field", "all", "any", "not"})
_VIEW_KEYS = frozenset({"freshness_breach", "maintenance_overdue", "p95_breach", "no_reads_days"})
_NO_READS_DAYS = range(1, 3661)


class BatchSelectionError(Exception):
    pass


class BatchSelectionCapExceeded(BatchSelectionError):
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.overflow = True
        super().__init__(f"This selection would exceed the {limit}-table batch limit; narrow it")


class BatchSelectionNotFound(BatchSelectionError):
    def __init__(self, receipt_id: uuid.UUID) -> None:
        self.receipt_id = receipt_id
        super().__init__("Selection receipt not found")


class BatchSelectionExpired(BatchSelectionError):
    def __init__(self, receipt_id: uuid.UUID) -> None:
        self.receipt_id = receipt_id
        super().__init__("Selection receipt expired; re-resolve it")


class BatchSelectionInvalid(BatchSelectionError):
    pass


@dataclass(frozen=True, slots=True)
class BatchTableRow:
    id: uuid.UUID
    catalog: str
    schema: str
    name: str


@dataclass(frozen=True, slots=True)
class BatchSelectionReceipt:
    receipt_id: uuid.UUID
    expires_at: datetime
    unique_count: int
    tables: tuple[BatchTableRow, ...]
    overflow: bool


@dataclass(frozen=True, slots=True)
class StoredReceipt:
    receipt_id: uuid.UUID
    expires_at: datetime
    table_ids: tuple[uuid.UUID, ...]
    principal: str
    tenant_id: uuid.UUID | None


class ReceiptStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._receipts: dict[uuid.UUID, StoredReceipt] = {}

    def put(self, receipt: StoredReceipt) -> None:
        with self._lock:
            self._receipts[receipt.receipt_id] = receipt

    def get(self, receipt_id: uuid.UUID) -> StoredReceipt | None:
        with self._lock:
            return self._receipts.get(receipt_id)

    def require(
        self,
        receipt_id: uuid.UUID,
        *,
        principal: str,
        tenant_id: uuid.UUID | None,
        now: datetime,
    ) -> StoredReceipt:
        receipt = self.get(receipt_id)
        if receipt is None or receipt.principal != principal or receipt.tenant_id != tenant_id:
            raise BatchSelectionNotFound(receipt_id)
        if receipt.expires_at <= now:
            raise BatchSelectionExpired(receipt_id)
        return receipt

    def clear(self) -> None:
        with self._lock:
            self._receipts.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._receipts)

    def __bool__(self) -> bool:
        return True


default_store = ReceiptStore()


def resolve_batch_selection(
    session: Session,
    *,
    principal: str,
    query_tenant_id: uuid.UUID,
    table_ids: Sequence[uuid.UUID] = (),
    filter_spec: Mapping[str, Any] | str | None = None,
    match_filter: bool = False,
    freshness_breach: bool = False,
    maintenance_overdue: bool = False,
    p95_breach: bool = False,
    no_reads_days: int | None = None,
    receipt_ids: Sequence[uuid.UUID] = (),
    exclude_ids: Sequence[uuid.UUID] = (),
    tenant_id: uuid.UUID | None = None,
    cap: int = DEFAULT_BATCH_CAP,
    ttl: timedelta = DEFAULT_RECEIPT_TTL,
    now: datetime | None = None,
    store: ReceiptStore | None = None,
) -> BatchSelectionReceipt:
    if cap < 1:
        raise BatchSelectionInvalid("batch cap must be at least 1")
    current = _aware(now or datetime.now(UTC))
    inner, flags = _split_filter(filter_spec)
    freshness_breach = freshness_breach or _flag(flags.get("freshness_breach"))
    maintenance_overdue = maintenance_overdue or _flag(flags.get("maintenance_overdue"))
    p95_breach = p95_breach or _flag(flags.get("p95_breach"))
    no_reads_days = _merge_days(no_reads_days, flags.get("no_reads_days"))
    view = build_table_view_filter(
        freshness_breach=freshness_breach,
        maintenance_overdue=maintenance_overdue,
        p95_breach=p95_breach,
        no_reads_days=no_reads_days,
        now=current,
    )
    predicate = _parse_predicate(inner)
    use_filter = match_filter or filter_spec is not None or view.clause is not None
    explicit = list(dict.fromkeys(table_ids))
    excluded = set(exclude_ids)
    if not explicit and not receipt_ids and not use_filter:
        raise BatchSelectionInvalid("table_ids, filter, or receipt_ids is required")

    receipts = default_store if store is None else store
    explicit_kept = set(explicit) - excluded
    if len(explicit_kept) > cap:
        raise BatchSelectionCapExceeded(cap)

    receipt_members = _union_receipts(
        receipts,
        receipt_ids,
        principal=principal,
        tenant_id=tenant_id,
        now=current,
    )
    kept = (explicit_kept | receipt_members) - excluded
    if len(kept) > cap:
        raise BatchSelectionCapExceeded(cap)

    _require_known_tables(session, explicit_kept, query_tenant_id)
    extra: set[uuid.UUID] = set()
    if use_filter:
        slots = cap - len(kept)
        probed = _filter_ids(
            session,
            predicate=predicate,
            view=view,
            query_tenant_id=query_tenant_id,
            exclude=kept | excluded,
            limit=slots + 1,
        )
        if len(probed) > slots:
            raise BatchSelectionCapExceeded(cap)
        extra = set(probed)

    membership = kept | extra
    if len(membership) > cap:
        raise BatchSelectionCapExceeded(cap)
    tables = _display_rows(session, membership, query_tenant_id=query_tenant_id)
    receipt_id = uuid.uuid4()
    expires_at = current + ttl
    receipts.put(
        StoredReceipt(
            receipt_id=receipt_id,
            expires_at=expires_at,
            table_ids=tuple(row.id for row in tables),
            principal=principal,
            tenant_id=tenant_id,
        )
    )
    return BatchSelectionReceipt(
        receipt_id=receipt_id,
        expires_at=expires_at,
        unique_count=len(tables),
        tables=tables,
        overflow=False,
    )


def read_batch_selection(
    session: Session,
    receipt_id: uuid.UUID,
    *,
    principal: str,
    query_tenant_id: uuid.UUID,
    tenant_id: uuid.UUID | None = None,
    now: datetime | None = None,
    store: ReceiptStore | None = None,
) -> BatchSelectionReceipt:
    current = _aware(now or datetime.now(UTC))
    stored = (default_store if store is None else store).require(
        receipt_id,
        principal=principal,
        tenant_id=tenant_id,
        now=current,
    )
    tables = _display_rows(session, set(stored.table_ids), query_tenant_id=query_tenant_id)
    return BatchSelectionReceipt(
        receipt_id=stored.receipt_id,
        expires_at=stored.expires_at,
        unique_count=len(tables),
        tables=tables,
        overflow=False,
    )


def _union_receipts(
    store: ReceiptStore,
    receipt_ids: Sequence[uuid.UUID],
    *,
    principal: str,
    tenant_id: uuid.UUID | None,
    now: datetime,
) -> set[uuid.UUID]:
    members: set[uuid.UUID] = set()
    for receipt_id in dict.fromkeys(receipt_ids):
        stored = store.require(receipt_id, principal=principal, tenant_id=tenant_id, now=now)
        members.update(stored.table_ids)
    return members


def _require_known_tables(
    session: Session, table_ids: set[uuid.UUID], query_tenant_id: uuid.UUID
) -> None:
    if not table_ids:
        return
    stmt = scope_by_tenant(select(Table.id).where(Table.id.in_(table_ids)), Table, query_tenant_id)
    found = set(session.scalars(stmt))
    missing = table_ids - found
    if missing:
        listed = ", ".join(sorted(str(item) for item in missing))
        raise BatchSelectionInvalid(f"Unknown table ids: {listed}")


def _filter_ids(
    session: Session,
    *,
    predicate: ColumnElement[bool] | None,
    view: TableViewFilter,
    query_tenant_id: uuid.UUID,
    exclude: set[uuid.UUID],
    limit: int,
) -> list[uuid.UUID]:
    # Same joins as GET /tables so predicate and view filters see the same columns.
    stmt = select(Table.id).outerjoin(TableStats).outerjoin(TableCost).outerjoin(TableAssignment)
    stmt = scope_by_tenant(stmt, Table, query_tenant_id)
    if predicate is not None:
        stmt = stmt.where(predicate)
    if view.clause is not None:
        stmt = stmt.where(view.clause)
    if exclude:
        stmt = stmt.where(Table.id.notin_(exclude))
    return list(session.scalars(stmt.order_by(Table.id).limit(limit)))


def _display_rows(
    session: Session, table_ids: set[uuid.UUID], *, query_tenant_id: uuid.UUID
) -> tuple[BatchTableRow, ...]:
    if not table_ids:
        return ()
    stmt = scope_by_tenant(
        select(Table, Source)
        .join(Source, Source.id == Table.source_id)
        .where(Table.id.in_(table_ids)),
        Table,
        query_tenant_id,
    )
    found = {table.id: (table, source) for table, source in session.execute(stmt)}
    missing = table_ids - set(found)
    if missing:
        raise BatchSelectionInvalid("Selection references a table that is not visible")
    rows = [
        BatchTableRow(
            id=table.id,
            catalog=catalog_name_for_source(source),
            schema=table.schema_name,
            name=table.table_name,
        )
        for table, source in found.values()
    ]
    rows.sort(key=lambda row: (row.catalog, row.schema, row.name, str(row.id)))
    return tuple(rows)


def _split_filter(
    raw: Mapping[str, Any] | str | None,
) -> tuple[Mapping[str, Any] | str | None, dict[str, Any]]:
    if not isinstance(raw, Mapping):
        return raw, {}
    if _PREDICATE_KEYS.intersection(raw):
        return raw, {}
    if "predicate" in raw or "filter" in raw or _VIEW_KEYS.intersection(raw):
        inner = raw.get("predicate", raw.get("filter"))
        if inner is not None and not isinstance(inner, Mapping | str):
            raise BatchSelectionInvalid("filter predicate must be a JSON object")
        return inner, {key: raw[key] for key in _VIEW_KEYS if key in raw}
    return raw, {}


def _parse_predicate(raw: Mapping[str, Any] | str | None) -> ColumnElement[bool] | None:
    if raw is None:
        return None
    payload: Any = raw
    if isinstance(raw, str):
        if not raw.strip():
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BatchSelectionInvalid(str(exc)) from exc
    if not isinstance(payload, Mapping):
        raise BatchSelectionInvalid("filter must be a JSON object")
    try:
        return compile_predicate(payload)
    except (TypeError, ValueError) as exc:
        raise BatchSelectionInvalid(str(exc)) from exc


def _flag(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    raise BatchSelectionInvalid("view filter flags must be booleans")


def _merge_days(explicit: int | None, nested: Any) -> int | None:
    if nested is None:
        return explicit
    if isinstance(nested, bool) or not isinstance(nested, int) or nested not in _NO_READS_DAYS:
        raise BatchSelectionInvalid("no_reads_days must be between 1 and 3660")
    if explicit is not None and explicit != nested:
        raise BatchSelectionInvalid("no_reads_days disagrees")
    return explicit if explicit is not None else nested


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
