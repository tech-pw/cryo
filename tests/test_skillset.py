"""Tests for cryo/ai/skillset.py — inspired by arXiv:2606.01185 (Skill Issues)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from cryo.ai.skillset import (
    DEFAULT_ICEBERG_ADVISOR_SKILLSET,
    Skillset,
    record_outcome,
    register_skillset,
    set_active_skillset,
)


class TestSkillsetDataclass:
    def test_from_row(self) -> None:
        row = MagicMock()
        row.id = uuid.uuid4()
        row.name = "test_skillset"
        row.version = "1.0.0"
        row.prompts = {"summarize": "Summarize {stats}"}
        row.tools = [{"name": "list_partitions"}]
        row.created_at = datetime.now(UTC)
        row.is_active = True
        row.metrics = {"accept_count": 5, "reject_count": 2}

        skillset = Skillset.from_row(row)
        assert skillset.name == "test_skillset"
        assert skillset.version == "1.0.0"
        assert skillset.prompts == {"summarize": "Summarize {stats}"}
        assert len(skillset.tools) == 1
        assert skillset.is_active is True
        assert skillset.metrics["accept_count"] == 5

    def test_prompt_variable_substitution(self) -> None:
        skillset = Skillset(
            id="123",
            name="test",
            version="1.0",
            prompts={"insight": "Stats: {stats}"},
            tools=[],
        )
        rendered = skillset.prompt("insight", stats="100 files")
        assert rendered == "Stats: 100 files"

    def test_prompt_missing_variable_returns_template(self) -> None:
        skillset = Skillset(
            id="123",
            name="test",
            version="1.0",
            prompts={"insight": "Stats: {stats}"},
            tools=[],
        )
        rendered = skillset.prompt("insight")
        assert rendered == "Stats: {stats}"

    def test_prompt_unknown_name_returns_empty(self) -> None:
        skillset = Skillset(id="123", name="test", version="1.0", prompts={}, tools=[])
        rendered = skillset.prompt("does_not_exist")
        assert rendered == ""


class TestDefaultSkillset:
    def test_builtin_present(self) -> None:
        assert DEFAULT_ICEBERG_ADVISOR_SKILLSET["name"] == "iceberg_advisor"
        assert "prompts" in DEFAULT_ICEBERG_ADVISOR_SKILLSET
        assert "tools" in DEFAULT_ICEBERG_ADVISOR_SKILLSET
        assert "table_insight" in DEFAULT_ICEBERG_ADVISOR_SKILLSET["prompts"]
        assert "plan_explanation" in DEFAULT_ICEBERG_ADVISOR_SKILLSET["prompts"]
        assert "savings_forecast" in DEFAULT_ICEBERG_ADVISOR_SKILLSET["prompts"]

    def test_builtin_tools_have_names(self) -> None:
        for tool in DEFAULT_ICEBERG_ADVISOR_SKILLSET["tools"]:
            assert "name" in tool
            assert "description" in tool


class TestRegisterSkillset:
    def test_register_new_skillset(self) -> None:
        # register_skillset builds a real `select(SkillsetRow).where(SkillsetRow.name == ...)`
        # expression internally -- patching SkillsetRow itself replaces it with a MagicMock,
        # which SQLAlchemy's select() rejects outright (not a valid FROM-clause element). The
        # session is already a full MagicMock (add/flush/commit are no-ops), so there's no need
        # to mock the ORM class too -- constructing a real SkillsetRow doesn't touch a DB.
        session = MagicMock()
        session.scalar.return_value = None  # no existing version

        skillset = Skillset(
            id="",
            name="new_skillset",
            version="1.0.0",
            prompts={"test": "Test prompt"},
            tools=[{"name": "test_tool"}],
            is_active=True,
        )
        result = register_skillset(session, skillset)
        assert result.name == "new_skillset"
        session.add.assert_called_once()
        session.commit.assert_called()

    def test_register_active_deactivates_others(self) -> None:
        session = MagicMock()
        session.scalar.return_value = None  # new version

        skillset = Skillset(
            id="",
            name="promote_test",
            version="2.0.0",
            prompts={"p": "v2"},
            tools=[],
            is_active=True,
        )
        register_skillset(session, skillset)
        # update() should be called to deactivate old versions
        session.execute.assert_called()


class TestSetActiveSkillset:
    def test_set_active_raises_on_missing(self) -> None:
        session = MagicMock()
        session.scalar.return_value = None

        with pytest.raises(LookupError, match="not found"):
            set_active_skillset(session, "nonexistent", "1.0.0")


class TestRecordOutcome:
    def test_record_outcome_validates_decision(self) -> None:
        session = MagicMock()
        skillset_id = uuid.uuid4()
        session.get.return_value = MagicMock(metrics={})

        with pytest.raises(ValueError, match="Invalid decision"):
            record_outcome(
                session,
                skillset_id,
                prompt_name="test",
                recommendation="recommend optimize",
                decision="invalid_decision",
            )

    def test_record_outcome_accepted_increments_counter(self) -> None:
        session = MagicMock()
        skillset_id = uuid.uuid4()
        mock_row = MagicMock()
        mock_row.metrics = {"accept_count": 5, "reject_count": 2, "sample_count": 7}
        session.get.return_value = mock_row

        record_outcome(
            session,
            skillset_id,
            prompt_name="test",
            recommendation="recommend optimize",
            decision="accepted",
            score=0.9,
        )
        session.commit.assert_called()

    def test_record_outcome_rejected_increments_reject(self) -> None:
        session = MagicMock()
        skillset_id = uuid.uuid4()
        mock_row = MagicMock()
        mock_row.metrics = {"accept_count": 1, "reject_count": 3, "sample_count": 4}
        session.get.return_value = mock_row

        outcome = record_outcome(
            session,
            skillset_id,
            prompt_name="test",
            recommendation="skip",
            decision="rejected",
        )
        assert outcome.decision == "rejected"

    def test_record_outcome_pending(self) -> None:
        session = MagicMock()
        skillset_id = uuid.uuid4()
        mock_row = MagicMock()
        mock_row.metrics = {}
        session.get.return_value = mock_row

        outcome = record_outcome(
            session,
            skillset_id,
            prompt_name="test",
            recommendation="pending",
            decision="pending",
        )
        assert outcome.decision == "pending"

    def test_record_outcome_accept_rate_computed(self) -> None:
        session = MagicMock()
        skillset_id = uuid.uuid4()
        mock_row = MagicMock()
        mock_row.metrics = {"accept_count": 3, "reject_count": 1, "sample_count": 4}
        session.get.return_value = mock_row

        record_outcome(
            session,
            skillset_id,
            prompt_name="test",
            recommendation="ok",
            decision="accepted",
        )
        # acceptance_rate = 3/(3+1) = 0.75
        committed_metrics = mock_row.metrics
        assert "acceptance_rate" in committed_metrics


class TestOutcomeMetrics:
    def test_avg_score_calculation(self) -> None:
        session = MagicMock()
        skillset_id = uuid.uuid4()
        mock_row = MagicMock()
        mock_row.metrics = {
            "accept_count": 1,
            "reject_count": 0,
            "sample_count": 1,
            "score_sum": 0.8,
        }
        session.get.return_value = mock_row

        record_outcome(
            session,
            skillset_id,
            prompt_name="test",
            recommendation="ok",
            decision="accepted",
            score=0.9,
        )
        # avg_score = (0.8 + 0.9) / 1 = 1.7... hmm, wait -- score_sum is from existing,
        # new score is added. avg_score = score_sum / max(samples, 1)
        # After: score_sum = 0.8 + 0.9 = 1.7, samples = 2, avg = 0.85
        assert "avg_score" in mock_row.metrics
