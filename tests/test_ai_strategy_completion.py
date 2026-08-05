"""Integration tests closing architecture.md §2 (AI layer) gaps."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from cryo.ai.pii import classify_column
from cryo.api.preview_cost import savings_projection_from_stats
from cryo.compliance.pii_gate import maintenance_blocked_for_untagged_pii
from cryo.config import Settings
from cryo.db.models import Source, Table, TableStats
from cryo.governor.admission import AdmissionConfig
from cryo.governor.bandit_feedback import finalize_admission, register_admission
from cryo.intelligence.outcomes import OUTCOME_SCHEMA_VERSION
from cryo.mcp.server import build_default_mcp_server
from cryo.ml.cost_integration import COST_MODEL_CACHE_VERSION, load_cached_cost_model


def test_pii_gate_blocks_untagged_email_column(session, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRYO_STATE_DIR", str(tmp_path))
    source = Source(id=uuid.uuid4(), name="s", type="rest", config={})
    session.add(source)
    table_id = uuid.uuid4()
    session.add(
        Table(
            id=table_id,
            source_id=source.id,
            schema_name="demo",
            table_name="users",
            is_active=True,
        )
    )
    session.add(
        TableStats(
            table_id=table_id,
            scan_at=datetime.now(UTC),
            needs_compaction=True,
            compaction_priority="HIGH",
            grade="C",
            overall_score=60,
            total_files=10,
            small_files_count=5,
            total_size_gb=1.0,
            raw={
                "column_quality": [
                    {
                        "column_name": "customer_email",
                        "null_count": 0,
                        "value_count": 100,
                        "null_rate": 0.0,
                    }
                ]
            },
        )
    )
    session.commit()

    reason = maintenance_blocked_for_untagged_pii(
        session,
        table_id,
        [{"type": "optimize"}],
    )
    assert reason is not None
    assert "PII" in reason


def test_pii_gate_allows_with_force(session) -> None:
    source = Source(id=uuid.uuid4(), name="s2", type="rest", config={})
    session.add(source)
    table_id = uuid.uuid4()
    session.add(
        Table(
            id=table_id,
            source_id=source.id,
            schema_name="demo",
            table_name="users",
            is_active=True,
        )
    )
    session.add(
        TableStats(
            table_id=table_id,
            scan_at=datetime.now(UTC),
            needs_compaction=True,
            compaction_priority="HIGH",
            grade="C",
            overall_score=60,
            total_files=10,
            small_files_count=5,
            total_size_gb=1.0,
            raw={"column_quality": [{"column_name": "user_email", "value_count": 100}]},
        )
    )
    session.commit()
    assert (
        maintenance_blocked_for_untagged_pii(
            session,
            table_id,
            [{"type": "optimize"}],
            job_params={"force": True, "pii_gate_bypass_authorized": True},
        )
        is None
    )


def test_bandit_feedback_persists_across_processes(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRYO_STATE_DIR", str(tmp_path))
    job_id = str(uuid.uuid4())
    config = AdmissionConfig(high_load_ratio=0.85)
    register_admission(job_id, config)
    pending_path = tmp_path / "admission_pending.json"
    assert pending_path.exists()
    finalize_admission(job_id, job_succeeded=True)
    pending = json.loads(pending_path.read_text())
    assert job_id not in pending
    bandit_path = tmp_path / "admission_bandit.json"
    assert not bandit_path.exists()


def test_learned_cost_enriches_projection(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRYO_STATE_DIR", str(tmp_path))
    cache = tmp_path / "cost_model_cache.json"
    cache.write_text(
        json.dumps(
            {
                "cache_version": COST_MODEL_CACHE_VERSION,
                "outcome_schema_version": OUTCOME_SCHEMA_VERSION,
                "weights": [0.5, 0.1, 0.1, 0.0, 0.0, 1.0, 0.0],
                "bias": 0.01,
                "residual_quantile": 0.05,
                "feature_min": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                "feature_max": [10.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
                "n_samples": 20,
            }
        )
    )
    stats = {
        "small_files_count": 100,
        "total_size_gb": 10.0,
        "delete_file_pct": 5.0,
        "raw": {"reclaim_optimize_bytes": 1_073_741_824},
    }
    base = savings_projection_from_stats(stats, Settings())
    assert "learned_cost" in base.assumptions


def test_legacy_answer_leaking_cost_cache_is_rejected(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRYO_STATE_DIR", str(tmp_path))
    (tmp_path / "cost_model_cache.json").write_text(
        json.dumps(
            {
                "weights": [0.5, 0.1, 0.1, 0.0, 0.0, 1.0, 0.0],
                "bias": 0.01,
                "residual_quantile": 0.05,
                "n_samples": 20,
            }
        )
    )

    assert load_cached_cost_model() is None


def test_mcp_server_includes_schema_and_snapshot_tools() -> None:
    def _schema(_args: dict) -> dict:
        return {"columns": []}

    def _history(_args: dict) -> dict:
        return {"history": []}

    server = build_default_mcp_server(
        get_table_schema=_schema,
        get_snapshot_history=_history,
    )
    names = {t["name"] for t in server.list_tools()}
    assert "get_table_schema" in names
    assert "get_snapshot_history" in names


def test_pii_classifies_email() -> None:
    match = classify_column("customer_email", "string", sample_value="a@b.com")
    assert match is not None
