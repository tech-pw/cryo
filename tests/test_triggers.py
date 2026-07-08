from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from cryo.triggers.evaluator import TriggerEvaluator, TriggerType
from cryo.triggers.signals import (
    CostAnomalySignal,
    FileDegradationSignal,
    QueryLatencySignal,
    SignalResult,
    any_signal_triggered,
)


def _table_id() -> uuid.UUID:
    return uuid.uuid4()


def _session_with_stats(total_files: int, small_files_count: int) -> MagicMock:
    session = MagicMock()
    stats = MagicMock()
    stats.total_files = total_files
    stats.small_files_count = small_files_count
    session.scalar.return_value = stats
    return session


def test_file_degradation_triggers_when_ratio_at_threshold() -> None:
    session = _session_with_stats(total_files=100, small_files_count=40)
    signal = FileDegradationSignal(threshold_ratio=0.40, min_total_files=20)
    result = signal.evaluate(session, _table_id())
    assert result.triggered is True
    assert result.signal_name == "file_degradation"
    assert result.metric_value == pytest.approx(0.40)


def test_file_degradation_skips_when_table_too_small() -> None:
    session = _session_with_stats(total_files=5, small_files_count=5)
    signal = FileDegradationSignal(threshold_ratio=0.40, min_total_files=20)
    result = signal.evaluate(session, _table_id())
    assert result.triggered is False
    assert "too small" in result.reason


def test_file_degradation_handles_no_stats() -> None:
    session = MagicMock()
    session.scalar.return_value = None
    signal = FileDegradationSignal()
    result = signal.evaluate(session, _table_id())
    assert result.triggered is False
    assert "No stats" in result.reason


def test_evaluator_admits_when_signal_triggered() -> None:
    evaluator = TriggerEvaluator()
    table_id = _table_id()
    fake_signal = SignalResult(
        triggered=True,
        signal_name="file_degradation",
        reason="40% small files",
    )
    session = MagicMock()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "cryo.triggers.evaluator.check_all_signals",
            lambda *_args, **_kwargs: [fake_signal],
        )
        result = evaluator.evaluate(session, table_id, is_due_for_schedule=False)
    assert result.should_run is True
    assert result.trigger_type is TriggerType.FILE_DEGRADATION
    assert result.priority_boost == 0.5


def test_evaluator_admits_scheduled_without_running_signals() -> None:
    evaluator = TriggerEvaluator()
    table_id = _table_id()
    session = MagicMock()
    result = evaluator.evaluate(session, table_id, is_due_for_schedule=True)
    assert result.should_run is True
    assert result.trigger_type is TriggerType.SCHEDULE
    # check_all_signals is called for forced-signal-check / not-due, but for a
    # scheduled run it's skipped.
    assert result.signal_results == []


def test_evaluator_rejects_when_no_signals_no_schedule() -> None:
    evaluator = TriggerEvaluator()
    table_id = _table_id()
    fake_signal = SignalResult(triggered=False, signal_name="file_degradation", reason="ok")
    session = MagicMock()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "cryo.triggers.evaluator.check_all_signals",
            lambda *_args, **_kwargs: [fake_signal],
        )
        result = evaluator.evaluate(session, table_id, is_due_for_schedule=False)
    assert result.should_run is False


def test_manual_trigger_overrides_everything() -> None:
    evaluator = TriggerEvaluator()
    table_id = _table_id()
    evaluator.request_manual_trigger(table_id, reason="incident-77")
    session = MagicMock()
    result = evaluator.evaluate(session, table_id, is_due_for_schedule=False)
    assert result.should_run is True
    assert result.trigger_type is TriggerType.MANUAL
    assert "incident-77" in result.reason


def test_manual_clear_restores_normal_behavior() -> None:
    evaluator = TriggerEvaluator()
    table_id = _table_id()
    evaluator.request_manual_trigger(table_id, reason="x")
    evaluator.clear_manual_trigger(table_id)
    fake_signal = SignalResult(triggered=False, signal_name="file_degradation", reason="ok")
    session = MagicMock()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "cryo.triggers.evaluator.check_all_signals",
            lambda *_args, **_kwargs: [fake_signal],
        )
        result = evaluator.evaluate(session, table_id, is_due_for_schedule=False)
    assert result.should_run is False
    assert result.trigger_type is TriggerType.SCHEDULE


def test_evaluate_batch_sorts_signal_triggered_first() -> None:
    evaluator = TriggerEvaluator()
    t1, t2 = uuid.uuid4(), uuid.uuid4()
    session = MagicMock()

    def fake_check(_session, table_id, _config=None):
        if table_id == t1:
            return [
                SignalResult(triggered=True, signal_name="query_latency", reason="spike"),
            ]
        return [SignalResult(triggered=False, signal_name="file_degradation", reason="ok")]

    def fake_session():
        return session

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "cryo.triggers.evaluator.check_all_signals", fake_check
        )
        results = evaluator.evaluate_batch(
            session, [t1, t2], schedule_map={t1: False, t2: True}
        )
    # Only admitted tables are returned, with signal-triggered first.
    assert [r.table_id for r in results] == [t1, t2]
    assert results[0].trigger_type is TriggerType.QUERY_LATENCY
    assert results[1].trigger_type is TriggerType.SCHEDULE


def test_evaluate_batch_drops_not_admitted() -> None:
    evaluator = TriggerEvaluator()
    t1, t2 = uuid.uuid4(), uuid.uuid4()
    session = MagicMock()

    def fake_check(_session, table_id, _config=None):
        return [SignalResult(triggered=False, signal_name="file_degradation", reason="ok")]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("cryo.triggers.evaluator.check_all_signals", fake_check)
        results = evaluator.evaluate_batch(
            session, [t1, t2], schedule_map={t1: False, t2: False}
        )
    assert results == []


def test_any_signal_triggered_helper() -> None:
    assert (
        any_signal_triggered(
            [SignalResult(triggered=False, signal_name="x", reason="ok")]
        )
        is False
    )
    assert (
        any_signal_triggered(
            [
                SignalResult(triggered=False, signal_name="x", reason="ok"),
                SignalResult(triggered=True, signal_name="y", reason="hit"),
            ]
        )
        is True
    )


def test_query_latency_signal_skips_when_not_enough_recent_queries() -> None:
    session = MagicMock()
    session.execute.return_value.mappings.return_value.one.return_value = {
        "avg_ms": 0.0,
        "count": 0,
    }
    signal = QueryLatencySignal(min_recent_queries=10)
    result = signal.evaluate(session, _table_id())
    assert result.triggered is False
    assert "Not enough recent queries" in result.reason


def test_cost_anomaly_triggers_above_threshold() -> None:
    session = MagicMock()
    cost_row = MagicMock()
    cost_row.projected_savings_usd_month = 75.0
    session.scalar.return_value = cost_row
    signal = CostAnomalySignal(min_savings_usd=50.0)
    result = signal.evaluate(session, _table_id())
    assert result.triggered is True
    assert result.metric_value == 75.0


def test_cost_anomaly_handles_no_data() -> None:
    session = MagicMock()
    session.scalar.return_value = None
    signal = CostAnomalySignal()
    result = signal.evaluate(session, _table_id())
    assert result.triggered is False
    assert "No cost data" in result.reason
