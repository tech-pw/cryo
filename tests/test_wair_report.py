from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from cryo.db.models import Source, Table, TableStats
from cryo.optimizer.wair import analyze_wair_boundaries


def test_wair_report_selects_boundary_files_from_stored_scan_sample(session: Session) -> None:
    table = _seed(
        session,
        raw={
            "wair_file_sample": [
                {
                    "file_path": "lower.parquet",
                    "size_bytes": 100,
                    "lower_bounds": {"value": 0},
                    "upper_bounds": {"value": 12},
                },
                {
                    "file_path": "interior.parquet",
                    "size_bytes": 100,
                    "lower_bounds": {"value": 12},
                    "upper_bounds": {"value": 18},
                },
                {
                    "file_path": "upper.parquet",
                    "size_bytes": 100,
                    "lower_bounds": {"value": 18},
                    "upper_bounds": {"value": 25},
                },
            ],
            "wair_query_ranges": [{"column": "value", "lower": 10, "upper": 20, "frequency": 5}],
        },
    )

    result = analyze_wair_boundaries(session, table.id)

    assert result.status == "analyzed"
    assert result.boundary_file_count == 2
    assert result.selected_file_paths == ("lower.parquet", "upper.parquet")


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
