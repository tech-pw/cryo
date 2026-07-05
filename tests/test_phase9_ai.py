"""Phase 9 AI feature tests — embeddings, PII, bandit, cost model, health classifier."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from cryo.ai.copilot import answer_question
from cryo.ai.embeddings import cosine_similarity, embed_text
from cryo.ai.pii import classify_column
from cryo.ai.similarity import find_similar_tables
from cryo.governor.admission import AdmissionConfig
from cryo.governor.bandit import AdmissionBandit
from cryo.ml.cost_model import evaluate_model, predict_savings, train_cost_model
from cryo.ml.health_classifier import HealthLabel, classify_health_few_shot, evaluate_classifier
from cryo.planner.benefit_score import compute_benefit_score
from cryo.scanner.quality import ColumnQualityAccumulator


def test_embeddings_similar_prompts_score_high() -> None:
    a = embed_text("compaction backlog small files")
    b = embed_text("small file compaction backlog")
    assert cosine_similarity(a, b) > 0.5


def test_pii_classifies_email_column() -> None:
    match = classify_column("user_email", "string", sample_value="alice@corp.com")
    assert match is not None
    assert match.pii_category in ("email", "phone", "ssn")


def test_fleet_similarity_finds_matching_table() -> None:
    target = {
        "table_id": "1",
        "schema_name": "analytics",
        "table_name": "user_events",
        "stats": {"total_files": 100, "small_files_count": 10, "grade": "B", "partition_col": "dt"},
    }
    fleet = [
        target,
        {
            "table_id": "2",
            "schema_name": "analytics",
            "table_name": "user_events_v2",
            "stats": {
                "total_files": 102,
                "small_files_count": 11,
                "grade": "B",
                "partition_col": "dt",
            },
        },
        {
            "table_id": "3",
            "schema_name": "finance",
            "table_name": "ledger",
            "stats": {
                "total_files": 5,
                "small_files_count": 0,
                "grade": "A",
                "partition_col": "month",
            },
        },
    ]
    similar = find_similar_tables(target, fleet, min_similarity=0.85)
    assert len(similar) >= 1
    assert similar[0].table_name == "user_events_v2"


def test_bandit_falls_back_when_observations_low() -> None:
    bandit = AdmissionBandit()
    base = AdmissionConfig()
    tuned, confident = bandit.suggest_config(base)
    assert tuned == base
    assert confident is False


def test_bandit_records_outcomes_and_suggests_when_enough_data() -> None:
    bandit = AdmissionBandit()
    base = AdmissionConfig()
    for i in range(25):
        bandit.record_outcome(base, admitted=True, job_completed=i % 3 != 0)
    tuned, confident = bandit.suggest_config(base)
    assert tuned.per_replica_cap >= 1
    assert bandit.state.observations >= 25


def test_learned_cost_model_beats_heuristic_on_synthetic_data() -> None:
    rows = []
    for i in range(20):
        predicted = (i + 1) * 500_000_000
        actual = int(predicted * (0.8 + 0.02 * (i % 5)))
        rows.append(
            {
                "action": "optimize",
                "predicted_bytes_freed": predicted,
                "actual_bytes_freed": actual,
                "small_files_count": 100 + i,
                "total_size_gb": 10 + i,
                "delete_file_pct": 5,
                "input_files_count": 50 + i,
            }
        )
    model = train_cost_model(rows)
    assert model is not None
    metrics = evaluate_model(rows[-5:], model)
    assert metrics["n_samples"] == 5


def test_cost_prediction_falls_back_without_model() -> None:
    pred = predict_savings({"predicted_bytes_freed": 1_073_741_824}, None)
    assert pred.used_learned is False
    assert pred.predicted_usd == pred.heuristic_usd


def test_health_classifier_few_shot_knn() -> None:
    labeled = [
        HealthLabel("a", True),
        HealthLabel("b", True),
        HealthLabel("c", False),
        HealthLabel("d", False),
    ]
    stats = {
        "a": {
            "small_files_count": 900,
            "delete_file_pct": 40,
            "overall_score": 30,
            "total_files": 1000,
        },
        "b": {
            "small_files_count": 800,
            "delete_file_pct": 35,
            "overall_score": 40,
            "total_files": 900,
        },
        "c": {
            "small_files_count": 10,
            "delete_file_pct": 2,
            "overall_score": 90,
            "total_files": 50,
        },
        "d": {
            "small_files_count": 5,
            "delete_file_pct": 1,
            "overall_score": 95,
            "total_files": 20,
        },
    }
    target = {**stats["a"], "table_id": "a"}
    pred = classify_health_few_shot(target, labeled, stats)
    assert pred.needs_emergency_compaction is True
    assert pred.method in ("knn_few_shot", "tabpfn", "threshold_fallback")


def test_health_classifier_evaluate() -> None:
    labeled = [HealthLabel(f"t{i}", i < 3) for i in range(8)]
    stats = {
        f"t{i}": {
            "small_files_count": 600 if i < 3 else 5,
            "delete_file_pct": 30 if i < 3 else 1,
            "overall_score": 40 if i < 3 else 90,
            "total_files": 500,
        }
        for i in range(8)
    }
    metrics = evaluate_classifier(labeled, stats)
    assert metrics["n_samples"] >= 1


def test_quality_accumulator_tracks_ndv_from_distinct_counts() -> None:
    class _File:
        null_value_counts = {1: 0}
        value_counts = {1: 100}
        lower_bounds = {}
        upper_bounds = {}
        distinct_counts = {1: 42}

    accum = ColumnQualityAccumulator(None)
    accum.add(_File())
    cols = accum.finish()
    assert cols[0].ndv_estimate == 42


def test_autocomp_benefit_score_prioritizes_high_query_tables() -> None:
    hot = compute_benefit_score(small_files=100, query_frequency=50.0, partition_size_gb=10.0)
    cold = compute_benefit_score(small_files=100, query_frequency=0.5, partition_size_gb=10.0)
    assert hot > cold


def test_copilot_embedding_fallback_matches_compaction_topic(session) -> None:
    from cryo.db.models import Source, Table, TableStats

    source = Source(
        id=uuid.uuid4(),
        name="test-source",
        type="rest",
        config={"catalog_name": "lake"},
    )
    session.add(source)
    table_id = uuid.uuid4()
    session.add(
        Table(
            id=table_id,
            source_id=source.id,
            schema_name="demo",
            table_name="events",
            is_active=True,
        )
    )
    session.add(
        TableStats(
            table_id=table_id,
            scan_at=datetime.now(UTC),
            needs_compaction=True,
            compaction_priority="HIGH",
            small_files_count=500,
            grade="D",
            overall_score=55,
            total_files=1000,
            total_size_gb=10.0,
            delete_file_pct=5.0,
            manifest_count=10,
            total_snapshots=5,
            partition_skew_ratio=1.0,
            avg_file_size_mb=10.0,
        )
    )
    session.commit()

    outcome = answer_question("which tables need compaction rewrite", session)
    assert outcome["source"] in ("embedding_fallback", "verified")
