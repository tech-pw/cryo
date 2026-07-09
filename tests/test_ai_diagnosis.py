from __future__ import annotations

from cryo.ai.diagnosis import diagnose_deterministic


def test_diagnose_deterministic_engine_oom() -> None:
    result = diagnose_deterministic(
        [
            "job error: Query failed because of Out Of Memory while planning",
            "audit status=FAILED action=optimize",
        ]
    )
    assert result.diagnosis_class == "engine_oom"
    assert result.confidence >= 0.8
    assert "memory" in result.runbook_step.lower()


def test_diagnose_deterministic_unknown_on_weak_evidence() -> None:
    result = diagnose_deterministic(
        [
            "job status failed",
            "no detailed error payload available",
        ]
    )
    assert result.diagnosis_class == "unknown"
    assert result.confidence <= 0.5
