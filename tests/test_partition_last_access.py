from __future__ import annotations

from datetime import date
from uuid import uuid4

from cryo.api.routes_archive import _bucket, _ingestion_healthy
from cryo.db.models import PartitionLastAccess, Source, Table
from cryo.scanner.s3_request_cost_ingest import _advance_last_access


def test_bucket_accepts_s3a_locations() -> None:
    assert _bucket("s3a://pw-data/gold/x/y") == "pw-data"
    assert _bucket("s3://pw-data/gold") == "pw-data"
    assert _bucket("hdfs://nn/x") is None
    assert _bucket(None) is None


def test_ingestion_unhealthy_without_config_or_with_error() -> None:
    assert _ingestion_healthy(None) is None


def test_last_access_only_moves_forward(session) -> None:
    source = Source(id=uuid4(), name=f"s-{uuid4()}", type="rest", config={})
    table = Table(
        id=uuid4(), source_id=source.id, schema_name="s", table_name="t", location="s3://b/t"
    )
    session.add(source)
    session.flush()
    session.add(table)
    session.flush()
    _advance_last_access(session, table.id, "dt=2026-01-01/", date(2026, 9, 1))
    _advance_last_access(session, table.id, "dt=2026-01-01/", date(2026, 8, 1))
    _advance_last_access(session, table.id, "dt=2026-01-01/", date(2026, 9, 20))
    session.flush()
    row = session.get(PartitionLastAccess, (table.id, "dt=2026-01-01/"))
    assert row.first_read_day == date(2026, 8, 1)
    assert row.last_read_day == date(2026, 9, 20)
