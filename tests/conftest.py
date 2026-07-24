from __future__ import annotations

import itertools
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from cryo.db.base import Base
from cryo.db.models import (
    AuditLog,
    AWSCostInsightConfig,
    AWSCurLineItem,
    AWSIntegration,
    AWSInventoryObject,
    Destination,
    LLMObservation,
    MaintenanceJob,
    MaintenanceRun,
    Preset,
    PresetRule,
    QueryLog,
    QueryPattern,
    S3BucketLogConfig,
    S3RequestCostDaily,
    SilentDataLossEvent,
    Source,
    Table,
    TableAlertDispatch,
    TableAlertSubscription,
    TablePartition,
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
    SilentDataLossEvent,
)
_AUTOINCREMENT_ID_MODELS = (TablePartition, TableStatsHistory, AuditLog, QueryLog, QueryPattern)
_next_autoincrement_id = itertools.count(1)


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_: JSONB, compiler: object, **kwargs: object) -> str:
    return "JSON"


@pytest.fixture(autouse=True)
def _test_jwt_secret() -> None:
    from tests.auth_helpers import ensure_test_jwt_secret

    ensure_test_jwt_secret()


@pytest.fixture()
def session() -> Iterator[Session]:
    _patch_sqlite_incompatible_defaults()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(text("ATTACH DATABASE ':memory:' AS cryo"))
        Base.metadata.create_all(connection)

    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    event.listen(session_factory, "before_flush", _assign_missing_uuid_primary_keys)
    event.listen(session_factory, "before_flush", _assign_missing_autoincrement_primary_keys)

    with session_factory() as db_session:
        yield db_session

    event.remove(session_factory, "before_flush", _assign_missing_uuid_primary_keys)
    event.remove(session_factory, "before_flush", _assign_missing_autoincrement_primary_keys)
    engine.dispose()


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
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if column.server_default is None:
                continue
            default = str(column.server_default.arg)
            if "::jsonb" in default or "gen_random_uuid" in default:
                column.server_default = None
