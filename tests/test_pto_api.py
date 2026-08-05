from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from cryo.db.models import (
    AuditLog,
    Destination,
    LayoutRecommendation,
    QueryPattern,
    Source,
    Table,
    TableStats,
)
from tests.auth_helpers import OPERATOR_HEADERS, VIEWER_HEADERS


def test_pto_recommend_endpoint_is_operator_only_and_shadow(api_db) -> None:
    table_id = _seed(api_db.session_factory, filter_count=300)

    denied = api_db.client.post(
        f"/api/v1/tables/{table_id}/pto/recommend",
        headers=VIEWER_HEADERS,
    )
    response = api_db.client.post(
        f"/api/v1/tables/{table_id}/pto/recommend",
        headers=OPERATOR_HEADERS,
    )

    assert denied.status_code == 403
    assert response.status_code == 200
    body = response.json()
    assert body["shadow"] is True
    assert body["model"] == "heuristic_v1"
    assert body["status"] == "pending"
    assert body["recommendation_id"]
    assert body["pto_params"]
    assert 0 < body["candidates_considered"] <= 64


def test_pto_recommend_endpoint_returns_explicit_abstention(api_db) -> None:
    table_id = _seed(api_db.session_factory, filter_count=20)

    response = api_db.client.post(
        f"/api/v1/tables/{table_id}/pto/recommend",
        headers=OPERATOR_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "abstained"
    assert response.json()["abstention_reason"] == "insufficient_query_history"


def test_pto_simulation_is_operator_only_and_abstains_without_observations(api_db) -> None:
    table_id = _seed(api_db.session_factory, filter_count=300)
    recommendation = api_db.client.post(
        f"/api/v1/tables/{table_id}/pto/recommend",
        headers=OPERATOR_HEADERS,
    )
    denied = api_db.client.post(
        f"/api/v1/tables/{table_id}/pto/simulate",
        headers=VIEWER_HEADERS,
    )
    response = api_db.client.post(
        f"/api/v1/tables/{table_id}/pto/simulate",
        headers=OPERATOR_HEADERS,
    )

    assert recommendation.status_code == 200
    assert denied.status_code == 403
    assert response.status_code == 200
    assert response.json()["status"] == "abstained"
    assert response.json()["abstention_reason"] == "insufficient_file_pruning_observations"


def test_pto_holdout_is_operator_only_and_never_claims_counterfactual_evidence(api_db) -> None:
    table_id = _seed(api_db.session_factory, filter_count=300)
    api_db.client.post(
        f"/api/v1/tables/{table_id}/pto/recommend",
        headers=OPERATOR_HEADERS,
    )

    denied = api_db.client.post(
        f"/api/v1/tables/{table_id}/pto/holdout",
        headers=VIEWER_HEADERS,
    )
    response = api_db.client.post(
        f"/api/v1/tables/{table_id}/pto/holdout",
        headers=OPERATOR_HEADERS,
    )

    assert denied.status_code == 403
    assert response.status_code == 200
    assert response.json()["status"] == "abstained"
    assert response.json()["qualifies_for_canary"] is False
    with api_db.session_factory() as session:
        recommendation = session.query(LayoutRecommendation).filter_by(table_id=table_id).one()
        assert (recommendation.detail or {})["holdout_evidence"]["schema"] == "pto-holdout/v1"
        audit = session.query(AuditLog).filter_by(table_id=table_id).one()
        assert audit.action == "pto_holdout_recorded"


def test_pto_controlled_shadow_is_bound_to_recorded_differential(api_db) -> None:
    table_id = _seed(api_db.session_factory, filter_count=300)
    api_db.client.post(
        f"/api/v1/tables/{table_id}/pto/recommend",
        headers=OPERATOR_HEADERS,
    )
    api_db.client.post(
        f"/api/v1/tables/{table_id}/layout-differential/record",
        headers=OPERATOR_HEADERS,
    )
    with api_db.session_factory() as session:
        recommendation = session.query(LayoutRecommendation).filter_by(table_id=table_id).one()
        differential = (recommendation.detail or {})["differential_oracle"]
    payload = {
        "experiment_id": "shadow-api-v1",
        "workload_sha256": "3" * 64,
        "dataset_sha256": "4" * 64,
        "pto_params_sha256": differential["pto_params_sha256"],
        "rewrite_scope_sha256": differential["rewrite_scope_sha256"],
        "control": {
            "query_count": 50,
            "query_signature_set_sha256": "3" * 64,
            "total_scan_bytes": 10_000,
            "total_files_read": 1_000,
            "error_count": 0,
            "p95_latency_ms": 100,
        },
        "candidate": {
            "query_count": 50,
            "query_signature_set_sha256": "3" * 64,
            "total_scan_bytes": 7_000,
            "total_files_read": 800,
            "error_count": 0,
            "p95_latency_ms": 105,
        },
        "isolated_clone": True,
    }

    denied = api_db.client.post(
        f"/api/v1/tables/{table_id}/pto/controlled-shadow",
        headers=VIEWER_HEADERS,
        json=payload,
    )
    response = api_db.client.post(
        f"/api/v1/tables/{table_id}/pto/controlled-shadow",
        headers=OPERATOR_HEADERS,
        json=payload,
    )

    assert denied.status_code == 403
    assert response.status_code == 200
    assert response.json()["qualifies_for_canary"] is True
    with api_db.session_factory() as session:
        recommendation = session.query(LayoutRecommendation).filter_by(table_id=table_id).one()
        assert (recommendation.detail or {})["holdout_evidence"]["schema"] == (
            "pto-controlled-shadow/v1"
        )


def test_pto_production_safety_is_operator_only_and_derived_from_records(api_db) -> None:
    table_id = _seed(api_db.session_factory, filter_count=300)
    api_db.client.post(
        f"/api/v1/tables/{table_id}/pto/recommend",
        headers=OPERATOR_HEADERS,
    )
    with api_db.session_factory() as session:
        destination = Destination(
            id=uuid4(),
            name=f"destination-{uuid4()}",
            engine="trino",
            enabled=True,
            config={
                "rollout_policy": {
                    "enabled": True,
                    "phase": "canary",
                    "window_minutes": 60,
                    "min_attempts": 1,
                }
            },
        )
        session.add(destination)
        session.commit()
        destination_id = destination.id

    denied = api_db.client.post(
        f"/api/v1/tables/{table_id}/pto/production-safety/{destination_id}",
        headers=VIEWER_HEADERS,
    )
    response = api_db.client.post(
        f"/api/v1/tables/{table_id}/pto/production-safety/{destination_id}",
        headers=OPERATOR_HEADERS,
    )

    assert denied.status_code == 403
    assert response.status_code == 200
    assert response.json()["qualifies_for_canary"] is False
    assert "rollout_observing" in response.json()["reasons"]
    with api_db.session_factory() as session:
        recommendation = session.query(LayoutRecommendation).filter_by(table_id=table_id).one()
        assert (recommendation.detail or {})["production_safety"]["schema"] == (
            "production-safety/v1"
        )


def test_layout_differential_exposes_engine_limitations(api_db) -> None:
    table_id = _seed(api_db.session_factory, filter_count=300)
    recommendation = api_db.client.post(
        f"/api/v1/tables/{table_id}/pto/recommend",
        headers=OPERATOR_HEADERS,
    )

    response = api_db.client.get(
        f"/api/v1/tables/{table_id}/layout-differential",
        headers=VIEWER_HEADERS,
    )

    assert recommendation.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body["canary_eligible"] is False
    assert body["status"] in {"not_equivalent", "equivalent"}
    assert body["trino"]["rendered_sql_sha256"]
    assert body["spark"]["rendered_sql_sha256"]


def test_layout_differential_record_is_operator_only_and_durable(api_db) -> None:
    table_id = _seed(api_db.session_factory, filter_count=300)
    api_db.client.post(
        f"/api/v1/tables/{table_id}/pto/recommend",
        headers=OPERATOR_HEADERS,
    )

    denied = api_db.client.post(
        f"/api/v1/tables/{table_id}/layout-differential/record",
        headers=VIEWER_HEADERS,
    )
    response = api_db.client.post(
        f"/api/v1/tables/{table_id}/layout-differential/record",
        headers=OPERATOR_HEADERS,
    )

    assert denied.status_code == 403
    assert response.status_code == 200
    assert response.json()["canary_eligible"] is False
    with api_db.session_factory() as session:
        recommendation = session.query(LayoutRecommendation).filter_by(table_id=table_id).one()
        assert (recommendation.detail or {})["differential_oracle"]["schema"] == (
            "layout-differential/v2"
        )
        audit = session.query(AuditLog).filter_by(table_id=table_id).one()
        assert audit.action == "layout_differential_recorded"


def test_layout_conformance_record_is_operator_only_and_advisory(api_db) -> None:
    table_id = _seed(api_db.session_factory, filter_count=300)
    api_db.client.post(
        f"/api/v1/tables/{table_id}/pto/recommend",
        headers=OPERATOR_HEADERS,
    )
    api_db.client.post(
        f"/api/v1/tables/{table_id}/layout-differential/record",
        headers=OPERATOR_HEADERS,
    )
    payload = _conformance_payload()

    denied = api_db.client.post(
        f"/api/v1/tables/{table_id}/layout-conformance/record",
        headers=VIEWER_HEADERS,
        json=payload,
    )
    response = api_db.client.post(
        f"/api/v1/tables/{table_id}/layout-conformance/record",
        headers=OPERATOR_HEADERS,
        json=payload,
    )

    assert denied.status_code == 403
    assert response.status_code == 200
    assert response.json()["conformant"] is True
    assert response.json()["qualifies_production_gate"] is False
    with api_db.session_factory() as session:
        recommendation = session.query(LayoutRecommendation).filter_by(table_id=table_id).one()
        assert (recommendation.detail or {})["output_conformance"]["schema"] == (
            "rewrite-output-conformance/v1"
        )
        audit = (
            session.query(AuditLog)
            .filter_by(table_id=table_id, action="rewrite_output_conformance_recorded")
            .one()
        )
        assert audit.status == "ADVISORY"


def _conformance_payload() -> dict[str, object]:
    snapshot = {
        "row_count": 100,
        "row_multiset_sha256": "1" * 64,
        "schema_sha256": "2" * 64,
        "partition_spec_sha256": "3" * 64,
        "touched_scope_sha256": ["4" * 64],
    }
    return {
        "fixture_id": "day-partition-bounded-v1",
        "provenance": "synthetic_fixture",
        "baseline": {**snapshot, "engine": "baseline", "touched_scope_sha256": []},
        "trino": {**snapshot, "engine": "trino"},
        "spark": {**snapshot, "engine": "spark"},
        "allowed_scope_sha256": ["4" * 64],
    }


def _seed(session_factory, *, filter_count: int) -> UUID:
    with session_factory() as session:
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
                total_files=1_000,
                small_files_count=20,
                total_size_gb=100.0,
                query_count_30d=filter_count,
                raw={"partition_spec": [], "sort_order": []},
            )
        )
        session.add(
            QueryPattern(
                id=1,
                table_id=table.id,
                column_name="region",
                filter_count=filter_count,
                equality_filter_count=filter_count,
                avg_selectivity=0.05,
            )
        )
        session.commit()
        return table.id
