from __future__ import annotations

from cryo.ai.diagnosis import _coerce_llm_result, diagnose_deterministic


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


def test_llm_diagnosis_drops_invented_citations() -> None:
    source = ["Query failed: FilterNode too complex"]
    result = _coerce_llm_result(
        {
            "class": "engine_oom",
            "confidence": 0.9,
            "evidence_lines": ["invented verbatim log line"],
            "runbook_step": "drop the table",
            "summary": "made up",
        },
        source,
    )
    assert result is not None
    assert result.evidence_lines == source
    assert "drop the table" not in result.runbook_step
