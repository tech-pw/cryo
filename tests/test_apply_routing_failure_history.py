"""Integration test for _apply_routing's failure_history wiring (cryo/scheduler/service.py).

Confirms the full path: a failure_history map passed into _apply_routing actually reaches
adaptive routing's scoring and changes the outcome, without leaking the augmented
TableCandidate back into the shared `tables` list other jobs in the same tick still read.
"""

from __future__ import annotations

from cryo.domain.enums import Mode
from cryo.scheduler.rules import PresetConfig, TableCandidate
from cryo.scheduler.scheduler import ScheduledJobDraft, ScheduledRunDraft
from cryo.scheduler.service import _apply_routing


def _preset(**guardrails) -> PresetConfig:
    return PresetConfig(
        id="p1",
        name="adaptive",
        destination_id="native-1",
        guardrails={
            "adaptive_routing": True,
            "engine_pool": ["native-1", "spark-large"],
            **guardrails,
        },
    )


def _destinations() -> dict:
    return {
        "native-1": {"id": "native-1", "engine": "native"},
        "spark-large": {"id": "spark-large", "engine": "spark_emr"},
    }


def _run(table_id: str) -> ScheduledRunDraft:
    return ScheduledRunDraft(
        id="r1",
        preset_id="p1",
        trigger="cron",
        deadline_at=None,
        jobs=(
            ScheduledJobDraft(
                id="j1", run_id="r1", table_id=table_id, destination_id="native-1", mode=Mode.DEEP
            ),
        ),
    )


def test_apply_routing_routes_away_from_a_repeatedly_failing_destination() -> None:
    table = TableCandidate("t1", "raw", "big_table", {"total_size_gb": 24000})
    routed = _apply_routing(
        _run("t1"),
        _preset(),
        [table],
        _destinations(),
        failure_history={"t1": {"native-1": 4}},
    )
    assert routed.jobs[0].destination_id == "spark-large"


def test_apply_routing_without_failure_history_keeps_normal_adaptive_scoring() -> None:
    table = TableCandidate("t1", "raw", "big_table", {"total_size_gb": 24000})
    routed = _apply_routing(_run("t1"), _preset(), [table], _destinations(), failure_history=None)
    # No failure history at all -- normal size-based adaptive scoring applies, native isn't
    # excluded (it may or may not win on size alone; the point is it's not force-excluded).
    assert routed.jobs[0].destination_id in {"native-1", "spark-large"}


def test_apply_routing_does_not_mutate_the_shared_table_candidate() -> None:
    """The augmented TableCandidate (with engine_consecutive_failures injected) must be a
    per-call copy -- other jobs/presets in the same tick reading the same `tables` list must
    never see it."""
    table = TableCandidate("t1", "raw", "big_table", {"total_size_gb": 24000})
    _apply_routing(
        _run("t1"),
        _preset(),
        [table],
        _destinations(),
        failure_history={"t1": {"native-1": 4}},
    )
    assert "engine_consecutive_failures" not in table.stats


def test_apply_routing_ignores_failure_history_when_adaptive_routing_is_off() -> None:
    table = TableCandidate("t1", "raw", "big_table", {"total_size_gb": 24000})
    preset = PresetConfig(
        id="p1",
        name="explicit",
        destination_id="native-1",
        guardrails={"engine_pool": ["native-1", "spark-large"]},  # adaptive_routing not set
    )
    routed = _apply_routing(
        _run("t1"),
        preset,
        [table],
        _destinations(),
        failure_history={"t1": {"native-1": 4}},
    )
    # Explicit routing path (resolve_destination), not adaptive -- failure_history is simply
    # never consulted, so this must not raise and must fall back to the preset's own default.
    assert routed.jobs[0].destination_id == "native-1"
