"""Tests for cryo/integrations/s3_bucket_logging.py::discover_buckets_for_source -- the
"S3 request-cost tracking" panel's bucket-discovery step.

Regression coverage for the actual production bug: every table location on this deployment used
the s3a:// scheme (Hadoop/Spark convention for Hive Metastore-backed tables), which
parse_s3_location silently rejected, so discovery found zero buckets despite thousands of tables
having a recorded location -- the panel showed "No buckets discovered yet" with no error.
"""

from __future__ import annotations

from uuid import uuid4

from cryo.db.models import Source, Table
from cryo.integrations.s3_bucket_logging import discover_buckets_for_source


def _seed_source(session) -> Source:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="hive", config={"uri": "x"})
    session.add(source)
    session.flush()
    return source


def test_discover_buckets_finds_s3a_scheme_locations(session) -> None:
    source = _seed_source(session)
    session.add(
        Table(
            id=uuid4(),
            source_id=source.id,
            schema_name="revenue",
            table_name="gold_stats_report",
            is_active=True,
            location="s3a://acme-lake-gold/gold/revenue/t",
        )
    )
    session.flush()

    configs = discover_buckets_for_source(session, source.id)

    assert [c.bucket for c in configs] == ["acme-lake-gold"]


def test_discover_buckets_is_idempotent_across_repeated_calls(session) -> None:
    source = _seed_source(session)
    session.add(
        Table(
            id=uuid4(),
            source_id=source.id,
            schema_name="s",
            table_name="t",
            is_active=True,
            location="s3a://bucket-a/s/t",
        )
    )
    session.flush()

    first = discover_buckets_for_source(session, source.id)
    session.commit()
    second = discover_buckets_for_source(session, source.id)

    assert len(first) == 1
    assert len(second) == 1
    assert first[0].id == second[0].id


def test_discover_buckets_returns_empty_when_no_locations_recorded(session) -> None:
    source = _seed_source(session)
    session.add(
        Table(
            id=uuid4(),
            source_id=source.id,
            schema_name="s",
            table_name="not_yet_scanned",
            is_active=True,
        )
    )
    session.flush()

    configs = discover_buckets_for_source(session, source.id)

    assert configs == []
