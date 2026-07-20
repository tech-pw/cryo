"""API tests for AutoComp §A endpoints: the candidates endpoint (§A.5) and the ROI drawer's
effectiveness/top-MOOP-partitions enrichment. Exercises the routes through a real TestClient +
sqlite-backed FastAPI app (api_db fixture), not just the underlying functions, since this is
where request/response schema mismatches would actually surface.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from cryo.db.models import Source, Table, TablePartition, TableStats
from tests.auth_helpers import VIEWER_HEADERS


def _seed_table_with_partitions(session_factory) -> uuid.UUID:
    with session_factory() as session:
        source = Source(id=uuid.uuid4(), name=f"source-{uuid.uuid4()}", type="rest", config={})
        table = Table(
            id=uuid.uuid4(),
            source_id=source.id,
            schema_name="analytics",
            table_name="events",
            is_active=True,
        )
        session.add_all([source, table])
        session.flush()

        stats = TableStats(
            table_id=table.id,
            scan_at=datetime.now(UTC),
            needs_compaction=True,
            compaction_priority="HIGH",
            grade="C",
            overall_score=50,
            total_files=200,
            small_files_count=150,
            total_size_gb=20.0,
            raw={
                "compaction_effectiveness": {
                    "effectiveness_ratio": 42.0,
                    "effectiveness": "good",
                    "run_count": 3,
                }
            },
        )
        session.add(stats)

        scan_at = datetime.now(UTC)
        session.add_all(
            [
                TablePartition(
                    id=1,
                    table_id=table.id,
                    partition_values={"dt": "2025-05-02"},
                    partition_fields=[],
                    partition_value="2025-05-02",
                    partition_size_gb=10.0,
                    small_files_in_partition_count=100,
                    scan_at=scan_at,
                    compaction_benefit_score=50.0,
                    query_frequency=5.0,
                    autocomp_traits={"t1": 1.0, "t2": 0.0, "t3": 0.0, "t4": 1.0, "s_c": 0.9},
                ),
                TablePartition(
                    id=2,
                    table_id=table.id,
                    partition_values={"dt": "2025-05-03"},
                    partition_fields=[],
                    partition_value="2025-05-03",
                    partition_size_gb=1.0,
                    small_files_in_partition_count=1,
                    scan_at=scan_at,
                    compaction_benefit_score=0.1,
                    query_frequency=0.0,
                    autocomp_traits={"t1": 0.0, "t2": 1.0, "t3": 0.0, "t4": 0.0, "s_c": -0.1},
                ),
            ]
        )
        session.commit()
        return table.id


def test_autocomp_candidates_endpoint_ranks_and_flags_below_threshold(api_db) -> None:
    table_id = _seed_table_with_partitions(api_db.session_factory)

    resp = api_db.client.get(
        f"/api/v1/tables/{table_id}/autocomp/candidates",
        params={"min_score": 1.0},
        headers=VIEWER_HEADERS,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["table_id"] == str(table_id)
    assert len(body["items"]) == 2

    by_value = {item["partition_value"]: item for item in body["items"]}
    assert by_value["2025-05-02"]["eligible"] is True
    assert by_value["2025-05-02"]["skip_reason"] is None
    assert by_value["2025-05-02"]["moop_score"] == 0.9

    assert by_value["2025-05-03"]["eligible"] is False
    assert by_value["2025-05-03"]["skip_reason"] == "below_min_benefit"

    assert len(body["skipped"]) == 1
    assert body["skipped"][0]["partition_value"] == "2025-05-03"

    assert body["effectiveness"]["effectiveness"] == "good"
    assert "compare" in body
    assert "autocomp_wins" in body["compare"]


def test_autocomp_candidates_endpoint_empty_table_has_no_items(api_db) -> None:
    with api_db.session_factory() as session:
        source = Source(id=uuid.uuid4(), name=f"source-{uuid.uuid4()}", type="rest", config={})
        table = Table(
            id=uuid.uuid4(),
            source_id=source.id,
            schema_name="analytics",
            table_name="unscanned",
            is_active=True,
        )
        session.add_all([source, table])
        session.commit()
        table_id = table.id

    resp = api_db.client.get(
        f"/api/v1/tables/{table_id}/autocomp/candidates", headers=VIEWER_HEADERS
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["skipped"] == []
    assert body["effectiveness"] is None


def test_roi_endpoint_includes_effectiveness_and_top_moop_partitions(api_db) -> None:
    table_id = _seed_table_with_partitions(api_db.session_factory)

    resp = api_db.client.get(
        f"/api/v1/smart-maintenance/tables/{table_id}/roi", headers=VIEWER_HEADERS
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["effectiveness"]["effectiveness"] == "good"
    assert len(body["top_moop_partitions"]) == 2
    # Ranked by moop_score DESC -- the hot partition (s_c=0.9) must come first.
    assert body["top_moop_partitions"][0]["partition_value"] == "2025-05-02"
    assert body["top_moop_partitions"][0]["moop_score"] == 0.9
