"""Critical vitals action plan, 2026-09-20, P1 "contain repeated NoSuchKey work safely":
cryo.db.repositories.sources.record_missing_metadata/clear_missing_metadata and
SQLJobStore.record_missing_metadata -- persisted, reversible containment for a table whose
registered location's metadata.json/manifest 404s in S3 but doesn't fit
deactivate_orphaned_evolving_catalog_entries' narrower location-IS-NULL heuristic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryo.db.models import Source, Table
from cryo.db.repositories.job_store import SQLJobStore
from cryo.db.repositories.sources import clear_missing_metadata, record_missing_metadata

NOW = datetime(2026, 9, 20, 17, 0, tzinfo=UTC)


def _seed_table(session, *, location: str | None = "s3a://bucket/real/path") -> Table:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="ns",
        table_name="tbl",
        is_active=True,
        location=location,
    )
    session.add_all([source, table])
    session.commit()
    return table


def test_record_missing_metadata_sets_reason_and_future_retry_after(session) -> None:
    table = _seed_table(session)

    record_missing_metadata(session, table.id, reason="NoSuchKey: metadata.json", now=NOW)
    session.commit()

    session.expire_all()
    reloaded = session.get(Table, table.id)
    assert reloaded.maintenance_missing_metadata_reason == "NoSuchKey: metadata.json"
    retry_after = reloaded.maintenance_retry_after
    if retry_after.tzinfo is None:
        retry_after = retry_after.replace(tzinfo=UTC)
    assert retry_after == NOW + timedelta(hours=24)
    # Containment is NOT deactivation -- is_active must be untouched.
    assert reloaded.is_active is True


def test_record_missing_metadata_is_idempotent_and_rearms_the_window(session) -> None:
    table = _seed_table(session)

    record_missing_metadata(session, table.id, reason="first failure", now=NOW)
    session.commit()
    later = NOW + timedelta(hours=1)
    record_missing_metadata(session, table.id, reason="still failing", now=later)
    session.commit()

    session.expire_all()
    reloaded = session.get(Table, table.id)
    assert reloaded.maintenance_missing_metadata_reason == "still failing"
    retry_after = reloaded.maintenance_retry_after
    if retry_after.tzinfo is None:
        retry_after = retry_after.replace(tzinfo=UTC)
    assert retry_after == later + timedelta(hours=24)


def test_clear_missing_metadata_releases_the_containment(session) -> None:
    table = _seed_table(session)
    record_missing_metadata(session, table.id, reason="was broken", now=NOW)
    session.commit()

    clear_missing_metadata(session, table.id)
    session.commit()

    session.expire_all()
    reloaded = session.get(Table, table.id)
    assert reloaded.maintenance_missing_metadata_reason is None
    assert reloaded.maintenance_retry_after is None


def test_sql_job_store_record_missing_metadata_delegates_correctly(session) -> None:
    table = _seed_table(session)
    store = SQLJobStore(lambda: session)

    store.record_missing_metadata(str(table.id), "Table not found: NoSuchKeyException")

    session.expire_all()
    reloaded = session.get(Table, table.id)
    assert reloaded.maintenance_missing_metadata_reason == "Table not found: NoSuchKeyException"
    assert reloaded.maintenance_retry_after is not None
    assert reloaded.is_active is True


def test_sql_job_store_record_missing_metadata_is_a_noop_for_unknown_table_id(session) -> None:
    store = SQLJobStore(lambda: session)
    # Must not raise.
    store.record_missing_metadata(str(uuid4()), "irrelevant")
