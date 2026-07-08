"""Tests for cryo/governor/qpp.py — Variance-aware QPP (arXiv:2606.03464)."""

from __future__ import annotations

import uuid
from unittest.mock import patch

from cryo.governor.capacity import evaluate_qpp_for_job
from cryo.governor.qpp import (
    DEFAULT_PER_FILE_MS,
    QPPFeatures,
    QPPModel,
    QPPPrediction,
    hash_query_text,
    heuristic_predict,
    predict_admission,
)


class TestQPPFeatures:
    def test_from_dict(self) -> None:
        table_id = uuid.uuid4()
        data = {
            "query_text_hash": "abc123",
            "table_id": str(table_id),
            "partition_count": 10,
            "files_scanned": 100,
            "is_cached": True,
            "cotenant_load_pct": 0.7,
            "hour_of_day": 14,
            "day_of_week": 2,
        }
        features = QPPFeatures.from_dict(data)
        assert features.query_text_hash == "abc123"
        assert features.table_id == table_id
        assert features.partition_count == 10
        assert features.files_scanned == 100
        assert features.is_cached is True
        assert features.cotenant_load_pct == 0.7
        assert features.hour_of_day == 14
        assert features.day_of_week == 2

    def test_to_dict_roundtrip(self) -> None:
        table_id = uuid.uuid4()
        features = QPPFeatures(
            query_text_hash="hash123",
            table_id=table_id,
            partition_count=5,
            files_scanned=50,
            is_cached=False,
            cotenant_load_pct=0.3,
            hour_of_day=10,
            day_of_week=1,
        )
        restored = QPPFeatures.from_dict(features.to_dict())
        assert restored.query_text_hash == features.query_text_hash
        assert restored.table_id == features.table_id
        assert restored.files_scanned == features.files_scanned


class TestHashQueryText:
    def test_hash_query_text_deterministic(self) -> None:
        h1 = hash_query_text("SELECT * FROM table")
        h2 = hash_query_text("SELECT * FROM table")
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_hash_query_text_changes(self) -> None:
        h1 = hash_query_text("SELECT 1")
        h2 = hash_query_text("SELECT 2")
        assert h1 != h2

    def test_hash_query_text_none(self) -> None:
        h = hash_query_text(None)
        assert h == "0" * 64


class TestHeuristicPredict:
    def test_heuristic_returns_prediction(self) -> None:
        features = QPPFeatures(
            query_text_hash="x",
            table_id=uuid.uuid4(),
            partition_count=10,
            files_scanned=100,
            is_cached=False,
            cotenant_load_pct=0.5,
            hour_of_day=10,
            day_of_week=1,
        )
        pred = heuristic_predict(
            features,
            per_file_ms=50.0,
            cotenant=1.0,
            cache_speedup=0.6,
        )
        assert isinstance(pred, QPPPrediction)
        assert pred.p50_ms > 0
        assert pred.p95_ms > pred.p50_ms
        assert pred.p99_ms > pred.p95_ms
        assert pred.confidence >= 0.0

    def test_cached_query_faster(self) -> None:
        base = QPPFeatures(
            query_text_hash="x",
            table_id=uuid.uuid4(),
            partition_count=10,
            files_scanned=100,
            is_cached=False,
            cotenant_load_pct=0.5,
            hour_of_day=10,
            day_of_week=1,
        )
        cached = QPPFeatures(
            query_text_hash="x",
            table_id=uuid.uuid4(),
            partition_count=10,
            files_scanned=100,
            is_cached=True,
            cotenant_load_pct=0.5,
            hour_of_day=10,
            day_of_week=1,
        )
        pred_base = heuristic_predict(base, per_file_ms=50.0, cotenant=1.0, cache_speedup=0.6)
        pred_cached = heuristic_predict(cached, per_file_ms=50.0, cotenant=1.0, cache_speedup=0.6)
        assert pred_cached.p50_ms < pred_base.p50_ms


class TestQPPModel:
    def test_model_initializes(self) -> None:
        model = QPPModel()
        assert model.per_file_ms == DEFAULT_PER_FILE_MS
        assert model.min_observations == 20

    def test_model_uses_heuristic_without_observations(self) -> None:
        model = QPPModel(min_observations=100)  # far more than available
        features = QPPFeatures(
            query_text_hash="x",
            table_id=uuid.uuid4(),
            partition_count=10,
            files_scanned=100,
            is_cached=False,
            cotenant_load_pct=0.5,
            hour_of_day=10,
            day_of_week=1,
        )
        pred = model.predict(features)
        assert pred.p50_ms > 0
        assert pred.p99_ms > pred.p95_ms > pred.p50_ms

    def test_model_exceeds_target(self) -> None:
        features = QPPFeatures(
            query_text_hash="x",
            table_id=uuid.uuid4(),
            partition_count=100,
            files_scanned=1000,
            is_cached=False,
            cotenant_load_pct=0.9,
            hour_of_day=10,
            day_of_week=1,
        )
        model = QPPModel()
        pred = model.predict(features)
        assert isinstance(pred.exceeds_target(60000.0), bool)

    def test_model_train_and_predict(self) -> None:
        model = QPPModel(min_observations=2)
        table_id = uuid.uuid4()
        observations = [
            (
                QPPFeatures(
                    query_text_hash="q1",
                    table_id=table_id,
                    partition_count=10,
                    files_scanned=100,
                    is_cached=False,
                    cotenant_load_pct=0.5,
                    hour_of_day=10,
                    day_of_week=1,
                ),
                5000.0,
            ),
            (
                QPPFeatures(
                    query_text_hash="q2",
                    table_id=table_id,
                    partition_count=20,
                    files_scanned=200,
                    is_cached=False,
                    cotenant_load_pct=0.5,
                    hour_of_day=10,
                    day_of_week=1,
                ),
                10000.0,
            ),
        ]
        model.train(observations)
        assert len(model._weights) >= 4
        assert model._last_train_size == 2


class TestPredictAdmission:
    def test_exceeds_target_tolerance(self) -> None:
        pred = QPPPrediction(p50_ms=1000.0, p95_ms=5000.0, p99_ms=8000.0, confidence=0.8)
        # p95=5000, target=6000, tolerance=1.5 -> 5000 < 9000 -> False
        assert pred.exceeds_target(6000.0) is False
        # p95=5000, target=3000, tolerance=1.5 -> 5000 > 4500 -> True
        assert pred.exceeds_target(3000.0) is True

    def test_predict_admission_refuses_high_p95(self) -> None:
        pred = QPPPrediction(p50_ms=5000.0, p95_ms=20000.0, p99_ms=30000.0, confidence=0.9)
        admit, predicted = predict_admission(pred, target_p95_ms=10000.0)
        assert admit is False
        assert predicted == 20000.0

    def test_predict_admission_allows_low_p95(self) -> None:
        pred = QPPPrediction(p50_ms=100.0, p95_ms=500.0, p99_ms=800.0, confidence=0.9)
        admit, predicted = predict_admission(pred, target_p95_ms=10000.0)
        assert admit is True
        assert predicted == 500.0


class TestEvaluateQppForJob:
    def test_disabled_always_admits(self) -> None:
        with patch("cryo.config.get_settings") as mock_settings:
            mock_settings.return_value.qpp_enabled = False
            admit, p95 = evaluate_qpp_for_job(
                table_id=None,
                files_scanned=100,
                partition_count=10,
                cotenant_load_pct=0.5,
                target_p95_ms=600000,
            )
            assert admit is True
            assert p95 == 0.0

    def test_no_table_id_admits(self) -> None:
        with patch("cryo.config.get_settings") as mock_settings:
            mock_settings.return_value.qpp_enabled = True
            mock_settings.return_value.qpp_min_observations = 20
            admit, p95 = evaluate_qpp_for_job(
                table_id=None,
                files_scanned=100,
                partition_count=10,
                cotenant_load_pct=0.5,
                target_p95_ms=600000,
            )
            assert admit is True
