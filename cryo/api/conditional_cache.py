"""Request coalescing (single-flight), input-version ETags, 304 Not Modified, and short-lived
response caching for heavy read endpoints (#533).

Design rules:
1. Scope isolation: every single-flight key, ETag, and cache entry incorporates the caller's
   ``role`` and ``tenant_id`` (never raw tokens or secrets). Results are never shared across
   roles or tenants.
2. Input-derived ETags: ETags hash cheap DB version stamps of every input that can alter the
   route's output. Before executing the expensive query, ``If-None-Match`` is checked and
   answered with ``304 Not Modified``.
3. Short-lived LRU cache: keyed by ``(route, scope, params, etag)`` with a bounded byte budget
   and short TTL so a cold request after a scan computes once and subsequent tabs/polls are
   served without re-running fleet aggregations. If any input changes, the ETag changes, so
   stale entries are bypassed immediately even before TTL expiry.
4. Single-flight coalescing: concurrent identical requests in-process wait on one leader
   thread/event and share its serialized JSON bytes. An optional short Redis lock coordinates
   across API replicas when Redis is reachable (fails open if Redis is down).
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import Request, Response
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.api.security import Principal
from cryo.db.models import (
    AuditLog,
    LayoutRecommendation,
    MaintenanceExclusion,
    MaintenanceJob,
    MaintenanceRun,
    Preset,
    PresetRule,
    QueryPattern,
    ScannerConfig,
    SharedS3AnomalyReport,
    Source,
    Table,
    TableAssignment,
    TableColumn,
    TableCost,
    TableOperationExclusion,
    TableSimilarityCluster,
    TableSimilarityPair,
    TableStats,
)
from cryo.observability.metrics import (
    api_coalesced_requests_total,
    api_not_modified_total,
    api_response_cache_requests_total,
)

DEFAULT_TTL_SECONDS = 60.0
DEFAULT_MAX_CACHE_BYTES = 32 * 1024 * 1024  # 32 MiB bounded in-process cache
REDIS_LOCK_TTL_SECONDS = 15
REDIS_CACHE_TTL_SECONDS = 60


def _stamp(value: object) -> str:
    if value is None:
        return "-"
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def principal_scope_key(principal: Principal) -> str:
    """Auth/tenant boundary included in every ETag, cache key, and single-flight key."""
    tenant = str(principal.tenant_id) if principal.tenant_id is not None else "global"
    roles = ",".join(sorted(role.value for role in principal.roles))
    return f"roles={roles}|tenant={tenant}"


def build_etag(
    route: str, scope: str, params: Mapping[str, Any], stamps: tuple[object, ...]
) -> str:
    """Build a strong quoted HTTP ETag from route, scope, query params, and DB stamps."""
    canonical_params = json.dumps(params, sort_keys=True, default=str, separators=(",", ":"))
    raw = "|".join([route, scope, canonical_params, *(_stamp(s) for s in stamps)])
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return f'"{digest}"'


def if_none_match_matches(header_value: str | None, etag: str) -> bool:
    """RFC 9110 §13.1.2 weak/strong comparison for If-None-Match."""
    if not header_value:
        return False
    stripped = header_value.strip()
    if stripped == "*":
        return True
    target = etag.removeprefix("W/").strip()
    for token in stripped.split(","):
        candidate = token.strip().removeprefix("W/").strip()
        if candidate == target:
            return True
    return False


# ==================== Input Version Stamp Builders ====================


def fleet_dashboard_stamps(
    session: Session, *, settings_fingerprint: str = ""
) -> tuple[object, ...] | None:
    """Version stamps for fleet overview & cost dashboards (/dashboards/datalake, /dashboards/cost).

    Covers:
    - Table count and active table count + lifecycle changes
    - Latest TableStats scan_at and row count
    - Latest TableCost scan_at, last_full_cost_scan_at, and row count
    - Latest MaintenanceRun created/finished and active/finished MaintenanceJob state
    - Latest AuditLog id (realized bytes freed)
    - ScannerConfig updated_at + relevant cost settings
    """
    if not hasattr(session, "execute") or not hasattr(session, "scalar"):
        return None
    table_row = session.execute(
        select(
            func.count(Table.id),
            func.max(Table.first_seen_at),
            func.max(Table.removed_from_catalog_at),
            func.max(Table.lifecycle_changed_at),
        )
    ).one()
    active_count = session.scalar(select(func.count(Table.id)).where(Table.is_active.is_(True)))
    stats_row = session.execute(
        select(func.count(TableStats.table_id), func.max(TableStats.scan_at))
    ).one()
    cost_row = session.execute(
        select(
            func.count(TableCost.table_id),
            func.max(TableCost.scan_at),
            func.max(TableCost.last_full_cost_scan_at),
        )
    ).one()
    run_row = session.execute(
        select(
            func.count(MaintenanceRun.id),
            func.max(MaintenanceRun.created_at),
            func.max(MaintenanceRun.finished_at),
        )
    ).one()
    job_row = session.execute(
        select(
            func.count(MaintenanceJob.id),
            func.max(MaintenanceJob.started_at),
            func.max(MaintenanceJob.finished_at),
        )
    ).one()
    running_jobs = session.scalar(
        select(func.count(MaintenanceJob.id)).where(
            MaintenanceJob.status.in_(("admitted", "running"))
        )
    )
    audit_max = session.scalar(select(func.max(AuditLog.id)))
    scanner_cfg = session.scalar(select(func.max(ScannerConfig.updated_at)))
    return (
        *table_row,
        active_count,
        *stats_row,
        *cost_row,
        *run_row,
        *job_row,
        running_jobs,
        audit_max,
        scanner_cfg,
        settings_fingerprint,
    )


def decisions_recommendations_stamps(session: Session) -> tuple[object, ...] | None:
    """Version stamps for GET /smart-maintenance/layout-recommendations and /overview."""
    if not hasattr(session, "execute") or not hasattr(session, "scalar"):
        return None
    rec_row = session.execute(
        select(
            func.count(LayoutRecommendation.id),
            func.max(LayoutRecommendation.updated_at),
            func.max(LayoutRecommendation.approved_at),
            func.max(LayoutRecommendation.dismissed_at),
            func.max(LayoutRecommendation.applied_at),
        )
    ).one()
    stats_row = session.execute(
        select(func.count(TableStats.table_id), func.max(TableStats.scan_at))
    ).one()
    table_row = session.execute(select(func.count(Table.id), func.max(Table.first_seen_at))).one()
    return (*rec_row, *stats_row, *table_row)


def insights_clusters_stamps(session: Session) -> tuple[object, ...] | None:
    """Version stamps for GET /dedup/clusters."""
    if not hasattr(session, "execute") or not hasattr(session, "scalar"):
        return None
    cluster_row = session.execute(
        select(
            func.max(TableSimilarityCluster.generation),
            func.count(TableSimilarityCluster.id),
            func.max(TableSimilarityCluster.computed_at),
        )
    ).one()
    pair_count = session.scalar(select(func.count(TableSimilarityPair.id)))
    stats_row = session.execute(
        select(func.count(TableStats.table_id), func.max(TableStats.scan_at))
    ).one()
    table_count = session.scalar(select(func.count(Table.id)))
    return (*cluster_row, pair_count, *stats_row, table_count)


def shared_s3_report_stamps(session: Session) -> tuple[object, ...] | None:
    """Version stamps for GET /anomalies/shared-s3."""
    if not hasattr(session, "execute") or not hasattr(session, "scalar"):
        return None
    row = session.execute(
        select(
            func.count(SharedS3AnomalyReport.id),
            func.max(SharedS3AnomalyReport.generated_at),
        )
    ).one()
    return tuple(row)


def path_to_a_stamps(session: Session, table: Table) -> tuple[object, ...] | None:
    """Version stamps for GET /tables/{table_id}/path-to-a.

    Covers:
    - table scan_at, score, grade, target_file_size_bytes, sort_order, distribution_mode
    - table lifecycle and lifecycle_changed_at
    - last job on this table (count + max finished_at/started_at)
    - property/config/override audit rows on this table
    - TableAssignment (assigned_at + param_overrides)
    - Preset & PresetRule versions (effective preset resolution)
    - per-table holds and operation exclusions
    - LayoutRecommendation, TableColumn count, QueryPattern count/filter sum, SharedS3 report
    """
    if not hasattr(session, "execute") or not hasattr(session, "scalar"):
        return None
    stats = session.get(TableStats, table.id)
    stats_stamp: tuple[object, ...] = (
        (
            stats.scan_at,
            stats.overall_score,
            stats.grade,
            stats.target_file_size_bytes,
            stats.has_sort_order,
            stats.write_distribution_mode,
            json.dumps(
                (stats.raw or {}).get("iceberg_properties") or {},
                sort_keys=True,
                default=str,
            ),
        )
        if stats is not None
        else ("no-stats",)
    )
    job_row = session.execute(
        select(
            func.count(MaintenanceJob.id),
            func.max(MaintenanceJob.started_at),
            func.max(MaintenanceJob.finished_at),
        ).where(MaintenanceJob.table_id == table.id)
    ).one()
    audit_max = session.scalar(select(func.max(AuditLog.id)).where(AuditLog.table_id == table.id))
    assignment = session.get(TableAssignment, table.id)
    assign_stamp = (
        (
            assignment.preset_id,
            assignment.assigned_at,
            assignment.pinned,
            json.dumps(assignment.param_overrides or {}, sort_keys=True, default=str),
        )
        if assignment is not None
        else ("no-assignment",)
    )
    preset_row = session.execute(select(func.count(Preset.id), func.max(Preset.updated_at))).one()
    rule_count = session.scalar(select(func.count(PresetRule.id)))
    excl_row = session.execute(
        select(
            func.count(TableOperationExclusion.operation),
            func.max(TableOperationExclusion.created_at),
        ).where(TableOperationExclusion.table_id == table.id)
    ).one()
    hold_row = session.execute(
        select(
            func.count(MaintenanceExclusion.id),
            func.max(MaintenanceExclusion.created_at),
        ).where(MaintenanceExclusion.source_id == table.source_id)
    ).one()
    rec_row = session.execute(
        select(
            func.count(LayoutRecommendation.id),
            func.max(LayoutRecommendation.updated_at),
            LayoutRecommendation.status,
        )
        .where(LayoutRecommendation.table_id == table.id)
        .group_by(LayoutRecommendation.status)
    ).all()
    col_row = session.execute(
        select(func.count(TableColumn.column_name), func.max(TableColumn.updated_at)).where(
            TableColumn.table_id == table.id
        )
    ).one()
    qp_row = session.execute(
        select(func.count(QueryPattern.id), func.sum(QueryPattern.filter_count)).where(
            QueryPattern.table_id == table.id
        )
    ).one()
    shared_max = session.scalar(select(func.max(SharedS3AnomalyReport.generated_at)))
    source = session.get(Source, table.source_id) if table.source_id else None
    source_updated = getattr(source, "updated_at", None)
    return (
        table.id,
        table.table_name,
        table.lifecycle or "active",
        table.lifecycle_changed_at,
        *stats_stamp,
        *job_row,
        audit_max,
        *assign_stamp,
        *preset_row,
        rule_count,
        *excl_row,
        *hold_row,
        repr(rec_row),
        *col_row,
        *qp_row,
        shared_max,
        source_updated,
    )


# ==================== Bounded Cache + Single-Flight Coordinator ====================


@dataclass
class _CacheEntry:
    body: bytes
    etag: str
    expires_at: float
    size: int


@dataclass
class _InFlight:
    event: threading.Event
    body: bytes | None = None
    error: BaseException | None = None


class ConditionalResultCache:
    """Thread-safe bounded LRU byte cache + single-flight request coalescer."""

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_bytes: int = DEFAULT_MAX_CACHE_BYTES,
        redis_getter: Callable[[], Any] | None = None,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_bytes = max_bytes
        self._redis_getter = redis_getter
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._total_bytes = 0
        self._inflight: dict[str, _InFlight] = {}

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._total_bytes = 0
            self._inflight.clear()

    def _get_cached(self, key: str, now: float) -> bytes | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.expires_at <= now:
                self._total_bytes -= entry.size
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return entry.body

    def _put_cached(self, key: str, etag: str, body: bytes, now: float) -> None:
        size = len(body)
        if size > self._max_bytes:
            return
        with self._lock:
            existing = self._entries.pop(key, None)
            if existing is not None:
                self._total_bytes -= existing.size
            while self._entries and self._total_bytes + size > self._max_bytes:
                _, evicted = self._entries.popitem(last=False)
                self._total_bytes -= evicted.size
            self._entries[key] = _CacheEntry(
                body=body,
                etag=etag,
                expires_at=now + self._ttl_seconds,
                size=size,
            )
            self._total_bytes += size

    def _redis(self) -> Any:
        if self._redis_getter is None:
            return None
        try:
            return self._redis_getter()
        except Exception:  # noqa: BLE001
            return None

    def serve(
        self,
        *,
        request: Request,
        route: str,
        principal: Principal,
        params: Mapping[str, Any],
        stamps: tuple[object, ...] | None,
        compute: Callable[[], Any],
    ) -> Any:
        if stamps is None:
            return compute()
        scope = principal_scope_key(principal)
        etag = build_etag(route, scope, params, stamps)
        headers = {
            "ETag": etag,
            "Cache-Control": "private, no-cache",
            "Vary": "Authorization",
        }

        if if_none_match_matches(request.headers.get("if-none-match"), etag):
            api_not_modified_total.labels(route=route).inc()
            return Response(status_code=304, headers=headers)

        cache_key = f"{route}|{scope}|{etag}"
        now = time.monotonic()
        cached = self._get_cached(cache_key, now)
        if cached is not None:
            api_response_cache_requests_total.labels(route=route, outcome="hit").inc()
            return Response(content=cached, media_type="application/json", headers=headers)

        # Single-flight coalescing across concurrent identical requests in-process.
        with self._lock:
            rechecked = self._get_cached_unlocked(cache_key, time.monotonic())
            if rechecked is not None:
                api_response_cache_requests_total.labels(route=route, outcome="hit").inc()
                return Response(content=rechecked, media_type="application/json", headers=headers)
            flight = self._inflight.get(cache_key)
            if flight is None:
                flight = _InFlight(event=threading.Event())
                self._inflight[cache_key] = flight
                leader = True
            else:
                leader = False

        if not leader:
            api_coalesced_requests_total.labels(route=route).inc()
            flight.event.wait(timeout=60.0)
            if flight.error is not None:
                raise flight.error
            if flight.body is not None:
                return Response(content=flight.body, media_type="application/json", headers=headers)

        api_response_cache_requests_total.labels(route=route, outcome="miss").inc()
        try:
            body = self._compute_with_optional_redis(cache_key, compute)
            self._put_cached(cache_key, etag, body, time.monotonic())
            flight.body = body
            return Response(content=body, media_type="application/json", headers=headers)
        except BaseException as exc:
            flight.error = exc
            raise
        finally:
            flight.event.set()
            with self._lock:
                self._inflight.pop(cache_key, None)

    def _get_cached_unlocked(self, key: str, now: float) -> bytes | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at <= now:
            self._total_bytes -= entry.size
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return entry.body

    def _compute_with_optional_redis(self, cache_key: str, compute: Callable[[], Any]) -> bytes:
        redis_client = self._redis()
        digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()[:32]
        redis_val_key = f"cryo:http_cache:{digest}"
        redis_lock_key = f"cryo:http_lock:{digest}"
        lock_token = uuid.uuid4().hex

        if redis_client is not None:
            try:
                shared = redis_client.get(redis_val_key)
                if isinstance(shared, bytes) and shared:
                    return shared
                acquired = bool(
                    redis_client.set(
                        redis_lock_key,
                        lock_token.encode("utf-8"),
                        nx=True,
                        ex=REDIS_LOCK_TTL_SECONDS,
                    )
                )
                if not acquired:
                    # Another replica is computing the same (route, scope, etag); poll briefly.
                    deadline = time.monotonic() + 5.0
                    while time.monotonic() < deadline:
                        time.sleep(0.05)
                        shared = redis_client.get(redis_val_key)
                        if isinstance(shared, bytes) and shared:
                            return shared
            except Exception:  # noqa: BLE001 - fail open to local compute
                redis_client = None

        try:
            result = compute()
            encoded = json.dumps(jsonable_encoder(result), separators=(",", ":")).encode("utf-8")
            if redis_client is not None and len(encoded) <= 2 * 1024 * 1024:
                try:
                    redis_client.set(redis_val_key, encoded, ex=REDIS_CACHE_TTL_SECONDS)
                except Exception:  # noqa: BLE001
                    pass
            return encoded
        finally:
            if redis_client is not None:
                try:
                    if redis_client.get(redis_lock_key) == lock_token.encode("utf-8"):
                        redis_client.delete(redis_lock_key)
                except Exception:  # noqa: BLE001
                    pass


def _default_redis() -> Any:
    from cryo.config import get_settings

    settings = get_settings()
    if settings.environment == "test":
        return None
    from cryo.queue.redis_queue import get_redis_client

    return get_redis_client()


conditional_cache = ConditionalResultCache(redis_getter=_default_redis)
