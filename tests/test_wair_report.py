from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from cryo.db.models import Destination, Source, Table, TableStats
from cryo.optimizer.wair import analyze_wair_boundaries

GIB = 1024**3

_FILES = [
    {
        "file_path": "lower.parquet",
        "size_bytes": GIB,
        "lower_bounds": {"value": 0},
        "upper_bounds": {"value": 12},
    },
    {
        "file_path": "interior.parquet",
        "size_bytes": GIB,
        "lower_bounds": {"value": 12},
        "upper_bounds": {"value": 18},
    },
    {
        "file_path": "upper.parquet",
        "size_bytes": GIB,
        "lower_bounds": {"value": 18},
        "upper_bounds": {"value": 25},
    },
]
# 1 CPU-ms per MB read, five queries in the window.
_RANGES = [
    {
        "column": "value",
        "lower": 10,
        "upper": 20,
        "frequency": 5,
        "cpu_ms": 1_000_000.0,
        "input_bytes": 1_000_000_000,
    }
]
_COLUMNS = [{"column_name": "value", "column_type": "long"}]


def test_wair_report_recommends_a_sort_rewrite_of_the_boundary_files(session: Session) -> None:
    table = _seed(
        session,
        raw={"wair_file_sample": _FILES, "wair_query_ranges": _RANGES, "column_quality": _COLUMNS},
    )
    session.add(
        Destination(id=uuid4(), name="spark", engine="spark_emr", config={"catalog": "ice"})
    )
    session.commit()

    result = analyze_wair_boundaries(session, table.id, rewrite_gb_per_hour=25.0)

    assert result.status == "analyzed"
    assert result.boundary_file_count == 2
    assert result.selected_file_paths == ("lower.parquet", "upper.parquet")
    assert result.clustering_column == "value"
    assert result.boundary_values == (10, 20)
    assert result.net_cpu_seconds > 0
    assert result.spark_sql is not None
    assert "sort_order => 'value ASC NULLS LAST'" in result.spark_sql
    assert "where => 'value = 10 OR value = 20'" in result.spark_sql
    assert f"table => 'ice.analytics.{table.table_name}'" in result.spark_sql


def test_wair_report_abstains_when_rewriting_costs_more_than_it_saves(session: Session) -> None:
    table = _seed(session, raw={"wair_file_sample": _FILES, "wair_query_ranges": _RANGES})

    result = analyze_wair_boundaries(
        session, table.id, rewrite_gb_per_hour=25.0, aggressiveness=10.0
    )

    assert result.status == "abstained"
    assert result.abstention_reason == "recluster_cost_exceeds_savings"
    assert result.boundary_file_count == 2


def test_wair_report_abstains_without_query_cost_or_a_measured_rewrite_rate(
    session: Session,
) -> None:
    no_cost = [{"column": "value", "lower": 10, "upper": 20, "frequency": 5}]
    uncosted = _seed(session, raw={"wair_file_sample": _FILES, "wair_query_ranges": no_cost})
    costed = _seed(session, raw={"wair_file_sample": _FILES, "wair_query_ranges": _RANGES})

    assert (
        analyze_wair_boundaries(session, uncosted.id, rewrite_gb_per_hour=25.0).abstention_reason
        == "missing_query_cost_evidence"
    )
    assert (
        analyze_wair_boundaries(session, costed.id).abstention_reason
        == "missing_measured_rewrite_rate"
    )


def test_wair_report_abstains_without_stored_file_sample(session: Session) -> None:
    table = _seed(session, raw={})

    result = analyze_wair_boundaries(session, table.id)

    assert result.status == "abstained"
    assert result.abstention_reason == "missing_wair_file_sample"


def _seed(session: Session, *, raw: dict[str, object]) -> Table:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="analytics",
        table_name=f"events_{uuid4().hex}",
        is_active=True,
    )
    session.add_all([source, table])
    session.flush()
    session.add(
        TableStats(
            table_id=table.id,
            scan_at=datetime.now(UTC),
            needs_compaction=False,
            compaction_priority="LOW",
            grade="B",
            overall_score=85,
            total_files=3,
            small_files_count=0,
            total_size_gb=1.0,
            raw=raw,
        )
    )
    session.commit()
    return table
