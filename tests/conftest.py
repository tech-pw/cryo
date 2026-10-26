from __future__ import annotations

import itertools
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.sql.elements import WithinGroup

from cryo.db.base import Base
from cryo.db.models import (
    AssistantActionProposal,
    AssistantApiKey,
    AssistantAuthorization,
    AssistantConversation,
    AssistantEvidence,
    AssistantFeedback,
    AssistantMessage,
    AssistantToolCall,
    AuditLog,
    AWSCostInsightConfig,
    AWSCurLineItem,
    AWSIntegration,
    AWSInventoryObject,
    DarkTableCandidate,
    Destination,
    FleetImpactHistory,
    LLMObservation,
    MaintenanceJob,
    MaintenanceRun,
    PartitionAccessDaily,
    Preset,
    PresetRule,
    QueryCostAttributionDaily,
    QueryLog,
    QueryPattern,
    S3BucketLogConfig,
    S3RequestCostDaily,
    SavedExplorerView,
    SharedS3AnomalyReport,
    SilentDataLossEvent,
    Source,
    Table,
    TableAlertDispatch,
    TableAlertSubscription,
    TableCostHistory,
    TablePartition,
    TableQueryActivity,
    TableStatsHistory,
)
from cryo.lineage.tracker import LineageEdge  # noqa: F401 — register table metadata
from cryo.security.abac import ABACPolicy  # noqa: F401 — register table metadata
from cryo.workflows.approvals import ApprovalRequest  # noqa: F401 — register table metadata
from tests.test_presets_api_db import api_db  # noqa: F401 — shared API+DB test client fixture

UUID_ID_MODELS = (
    Source,
    Destination,
    Preset,
    PresetRule,
    Table,
    MaintenanceRun,
    MaintenanceJob,
    AWSIntegration,
    AWSCostInsightConfig,
    AWSCurLineItem,
    AWSInventoryObject,
    LLMObservation,
    TableAlertDispatch,
    TableAlertSubscription,
    S3BucketLogConfig,
    S3RequestCostDaily,
    SavedExplorerView,
    SilentDataLossEvent,
    DarkTableCandidate,
    SharedS3AnomalyReport,
    PartitionAccessDaily,
    AssistantConversation,
    AssistantMessage,
    AssistantToolCall,
    AssistantEvidence,
    AssistantActionProposal,
    AssistantAuthorization,
    AssistantFeedback,
    AssistantApiKey,
)
_AUTOINCREMENT_ID_MODELS = (
    TablePartition,
    TableStatsHistory,
    TableCostHistory,
    TableQueryActivity,
    AuditLog,
    QueryLog,
    QueryPattern,
    QueryCostAttributionDaily,
    FleetImpactHistory,
)
_next_autoincrement_id = itertools.count(1)


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_: JSONB, compiler: object, **kwargs: object) -> str:
    return "JSON"


@event.listens_for(Engine, "connect")
def _register_sqlite_jsonb_array_length(
    dbapi_connection: object, connection_record: object
) -> None:
    """Postgres's jsonb_array_length() (used by get_table_query_patterns) has no sqlite
    equivalent registered by default -- sqlite's own json_array_length() does the same thing
    for the JSON-typed columns _compile_jsonb_sqlite maps JSONB onto, so just alias it. Only
    fires for sqlite connections (checked via a cheap attribute probe rather than importing
    the pysqlite driver here) so this is a no-op against the real Postgres test path."""
    if not hasattr(dbapi_connection, "create_function"):
        return
    import json as _json

    def _jsonb_array_length(value: object) -> int | None:
        if value is None:
            return None
        parsed = _json.loads(value) if isinstance(value, str) else value
        return len(parsed) if isinstance(parsed, list) else None

    def _jsonb_typeof(value: object) -> str | None:
        # Postgres jsonb_typeof: SQL NULL -> NULL, JSON null -> 'null' (#255).
        if value is None:
            return None
        parsed = _json.loads(value) if isinstance(value, str) else value
        if parsed is None:
            return "null"
        if isinstance(parsed, bool):
            return "boolean"
        if isinstance(parsed, int | float):
            return "number"
        return {list: "array", dict: "object", str: "string"}.get(type(parsed), "string")

    dbapi_connection.create_function("jsonb_array_length", 1, _jsonb_array_length)
    dbapi_connection.create_function("jsonb_typeof", 1, _jsonb_typeof)


@event.listens_for(Engine, "connect")
def _register_sqlite_percentile_cont(dbapi_connection: object, connection_record: object) -> None:
    """Postgres's ``percentile_cont(p) WITHIN GROUP (ORDER BY col)`` (used by
    compaction_candidates._table_p95_latency) has no sqlite equivalent -- sqlite doesn't support
    WITHIN GROUP syntax at all, so this can't be a same-shape function alias like
    jsonb_array_length above; instead we register a sqlite aggregate class that sqlite invokes
    per matching row and finishes with a linear-interpolation percentile, called via a plain
    ``percentile_cont(col, p)`` from a query rewrite (see _rewrite_percentile_cont_for_sqlite in
    _patch_sqlite_incompatible_defaults below). Only fires for sqlite connections; no-op against
    the real Postgres test path."""
    if not hasattr(dbapi_connection, "create_aggregate"):
        return

    class _PercentileCont:
        def __init__(self) -> None:
            self.values: list[float] = []
            self.percentile: float | None = None

        def step(self, value: float | None, percentile: float | None) -> None:
            if value is not None:
                self.values.append(float(value))
            if percentile is not None:
                self.percentile = float(percentile)

        def finalize(self) -> float | None:
            if not self.values or self.percentile is None:
                return None
            ordered = sorted(self.values)
            if len(ordered) == 1:
                return ordered[0]
            rank = self.percentile * (len(ordered) - 1)
            lo, hi = int(rank), min(int(rank) + 1, len(ordered) - 1)
            frac = rank - lo
            return ordered[lo] + (ordered[hi] - ordered[lo]) * frac

    dbapi_connection.create_aggregate("percentile_cont_agg", 2, _PercentileCont)


@compiles(WithinGroup, "sqlite")
def _compile_within_group_sqlite(element: WithinGroup, compiler: object, **kwargs: object) -> str:
    """Rewrite Postgres' ``percentile_cont(p) WITHIN GROUP (ORDER BY col)`` into a call to the
    ``percentile_cont_agg(col, p)`` sqlite aggregate registered above -- sqlite has no WITHIN
    GROUP syntax at all, so this can't stay a plain function-name alias."""
    func_name = element.element.name
    if func_name != "percentile_cont":
        # Only percentile_cont is special-cased; anything else falls back to sqlite's default
        # (likely still broken, but not something this codebase's tests currently exercise).
        return compiler.visit_withingroup(element, **kwargs)
    percentile_expr = compiler.process(list(element.element.clauses)[0], **kwargs)
    order_by_expr = compiler.process(element.order_by, **kwargs)
    return f"percentile_cont_agg({order_by_expr}, {percentile_expr})"


@pytest.fixture(autouse=True)
def _test_jwt_secret() -> None:
    from tests.auth_helpers import ensure_test_jwt_secret

    ensure_test_jwt_secret()


@pytest.fixture(autouse=True)
def _fresh_fleet_insights_cache() -> Iterator[None]:
    """The /advanced/anomalies and /advanced/signals responses are cached per process (#259);
    each test gets its own in-memory database, so a cached fleet result must not leak."""
    import sys

    def clear() -> None:
        module = sys.modules.get("cryo.api.advanced_routes")
        if module is not None:
            module.clear_fleet_insights_cache()

    clear()
    yield
    clear()


@pytest.fixture()
def _session_maker() -> Iterator[sessionmaker]:
    """Shared engine/sessionmaker setup backing both the `session` and `session_factory`
    fixtures. StaticPool means every session opened against this engine sees the same
    in-memory database, so code under test (like run_finalizer) that opens its own session via
    a factory -- rather than reusing a caller-held session -- still reads/writes the same data
    the test set up."""
    restore_defaults = _patch_sqlite_incompatible_defaults()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    try:
        with engine.begin() as connection:
            connection.execute(text("ATTACH DATABASE ':memory:' AS cryo"))
            Base.metadata.create_all(connection)

        maker = sessionmaker(
            bind=engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )
        event.listen(maker, "before_flush", _assign_missing_uuid_primary_keys)
        event.listen(maker, "before_flush", _assign_missing_autoincrement_primary_keys)

        yield maker

        event.remove(maker, "before_flush", _assign_missing_uuid_primary_keys)
        event.remove(maker, "before_flush", _assign_missing_autoincrement_primary_keys)
        engine.dispose()
    finally:
        # Restore the real server_default values this fixture cleared above -- see
        # _patch_sqlite_incompatible_defaults's docstring for the real-Postgres-test-corruption
        # bug this prevents. Runs even if create_all/yield raised, so a failed test doesn't
        # leave the shared model classes patched for the rest of the process either.
        for column, original_default in restore_defaults:
            column.server_default = original_default


@pytest.fixture()
def session(_session_maker: sessionmaker) -> Iterator[Session]:
    with _session_maker() as db_session:
        yield db_session


@pytest.fixture()
def session_factory(_session_maker: sessionmaker) -> sessionmaker:
    """The bare sessionmaker, for code under test that takes a session *factory* (opens and
    commits its own session) rather than a caller-held session -- e.g.
    cryo.db.repositories.run_finalizer.finalize_open_runs."""
    return _session_maker


def _assign_missing_autoincrement_primary_keys(
    session: Session,
    flush_context: object,
    instances: object,
) -> None:
    for item in session.new:
        if not isinstance(item, _AUTOINCREMENT_ID_MODELS):
            continue
        mapper = inspect(item.__class__)
        pk_cols = mapper.primary_key
        if len(pk_cols) != 1:
            continue
        pk_attr = pk_cols[0].key
        if getattr(item, pk_attr) is None:
            setattr(item, pk_attr, next(_next_autoincrement_id))


def _assign_missing_uuid_primary_keys(
    session: Session,
    flush_context: object,
    instances: object,
) -> None:
    for item in session.new:
        if isinstance(item, UUID_ID_MODELS) and item.id is None:
            item.id = uuid.uuid4()


def _patch_sqlite_incompatible_defaults() -> None:
    """Clears server_default on columns SQLite can't evaluate (::jsonb casts, gen_random_uuid())
    so Base.metadata.create_all() succeeds against the in-memory SQLite engine.

    Real bug fixed here, 2026-09-20: this used to mutate column.server_default on the SAME
    shared Base.metadata / model classes every real-Postgres test file also imports (e.g.
    cryo.db.models.AnalysisTask) -- once any test using the `session`/`session_factory`
    fixtures ran (even in an unrelated file, earlier in the same pytest process), AnalysisTask.
    id's real `gen_random_uuid()` server_default was gone from the shared model class for the
    rest of the process. Any later real-Postgres test (test_analysis_tasks_postgres.py and
    friends, which insert AnalysisTask rows and rely on Postgres generating the UUID) then
    failed with a NULL primary key -- not because those tests were wrong, but because an
    unrelated SQLite-only fixture had already permanently vandalized the shared column
    definition. Restoring the original server_default values via a fixture finalizer (see
    _session_maker below) makes the patch properly scoped to one fixture's lifetime instead of
    process-lifetime, matching every other fixture's teardown discipline in this file.

    Returns the original (column, server_default) pairs so the caller can restore them.
    """
    originals: list[tuple[object, object]] = []
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if column.server_default is None:
                continue
            default = str(column.server_default.arg)
            if "::jsonb" in default or "gen_random_uuid" in default:
                originals.append((column, column.server_default))
                column.server_default = None
    return originals
