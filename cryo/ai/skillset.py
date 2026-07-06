"""Versioned AI skillset registry.

Based on: "Skill issues: A taxonomy of optimization failure modes in LLM
training" — arXiv:2606.01185. Treats prompt templates and tool
definitions as the optimization target, scoring them by their downstream
outcomes so we can promote higher-performing versions.

A *skillset* is a named bundle of prompts (keyed by ``prompt_name``) plus
an ordered list of tool definitions. Skillsets are stored in
``cryo.skillsets``; the ``metrics`` JSONB blob carries rolling outcome
stats (acceptance rate, mean score, sample count) updated whenever a
``SkillsetOutcome`` is recorded.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from cryo.db.models import Skillset as SkillsetRow
from cryo.db.models import SkillsetOutcome

logger = logging.getLogger(__name__)

JsonDict = dict[str, Any]
JsonList = list[JsonDict]


# ---------------------------------------------------------------------------
# Built-in default skillset
# ---------------------------------------------------------------------------

DEFAULT_ICEBERG_ADVISOR_SKILLSET: JsonDict = {
    "name": "iceberg_advisor",
    "version": "1.0.0",
    "prompts": {
        "table_insight": (
            "You are an Apache Iceberg expert. Given the following table "
            "statistics, summarize the table's health and the top three issues "
            "in 3-5 bullet points. Reference specific metrics.\n\n"
            "Stats:\n{stats}\n\n"
            "Be concise and actionable."
        ),
        "plan_explanation": (
            "You are an Iceberg maintenance planner. Explain the following "
            "compaction plan to a data engineer in plain English. Cover the "
            "what, why, and expected impact.\n\n"
            "Plan:\n{plan}\n\n"
            "Use 4-6 short sentences."
        ),
        "savings_forecast": (
            "You are a FinOps analyst for an Iceberg lakehouse. Given current "
            "monthly storage cost and projected savings, write a one-paragraph "
            "executive summary that quantifies the dollar impact and calls out "
            "the top contributors.\n\n"
            "Data:\n{forecast}"
        ),
    },
    "tools": [
        {
            "name": "list_partitions",
            "description": "List the partitions of a scanned table with small-file counts.",
            "parameters": {"table_id": "uuid"},
        },
        {
            "name": "get_top_compaction_candidates",
            "description": "Return the top AutoComp-ranked partitions for a table.",
            "parameters": {"table_id": "uuid", "k": "int"},
        },
        {
            "name": "forecast_cost",
            "description": "Project next month's storage cost and savings.",
            "parameters": {"budget_limit_usd": "float?"},
        },
    ],
}


@dataclass
class Skillset:
    id: str
    name: str
    version: str
    prompts: JsonDict = field(default_factory=dict)
    tools: JsonList = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    is_active: bool = False
    metrics: JsonDict = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: SkillsetRow) -> Skillset:
        return cls(
            id=str(row.id),
            name=row.name,
            version=row.version,
            prompts=dict(row.prompts or {}),
            tools=list(row.tools or []),
            created_at=row.created_at,
            is_active=bool(row.is_active),
            metrics=dict(row.metrics or {}),
        )

    def prompt(self, prompt_name: str, **variables: Any) -> str:
        template = str(self.prompts.get(prompt_name, ""))
        if not variables:
            return template
        try:
            return template.format(**variables)
        except (KeyError, IndexError):
            return template


def _ensure_builtin_default(session: Session) -> Skillset | None:
    """Insert the built-in default skillset if the table is empty."""
    existing = session.scalar(select(func.count(SkillsetRow.id)))
    if existing and existing > 0:
        return None
    builtin = SkillsetRow(
        name=DEFAULT_ICEBERG_ADVISOR_SKILLSET["name"],
        version=DEFAULT_ICEBERG_ADVISOR_SKILLSET["version"],
        prompts=DEFAULT_ICEBERG_ADVISOR_SKILLSET["prompts"],
        tools=DEFAULT_ICEBERG_ADVISOR_SKILLSET["tools"],
        is_active=True,
        metrics={"accept_count": 0, "reject_count": 0, "sample_count": 0},
    )
    session.add(builtin)
    session.flush()
    return Skillset.from_row(builtin)


def load_skillset(session: Session, name: str) -> Skillset:
    """Load a skillset by name. Returns the active version, or the latest
    version if no version is active, or the built-in default if the table
    is empty."""
    seeded = _ensure_builtin_default(session)
    if seeded is not None and seeded.name == name:
        session.commit()
        return seeded

    row = session.scalar(
        select(SkillsetRow).where(
            SkillsetRow.name == name, SkillsetRow.is_active.is_(True)
        ).limit(1)
    )
    if row is not None:
        return Skillset.from_row(row)

    row = session.scalar(
        select(SkillsetRow)
        .where(SkillsetRow.name == name)
        .order_by(SkillsetRow.created_at.desc())
        .limit(1)
    )
    if row is not None:
        return Skillset.from_row(row)

    # Empty table — return the built-in (without persisting since the caller
    # may want a transient skillset to compare against).
    return Skillset(
        id="",
        name=DEFAULT_ICEBERG_ADVISOR_SKILLSET["name"],
        version=DEFAULT_ICEBERG_ADVISOR_SKILLSET["version"],
        prompts=DEFAULT_ICEBERG_ADVISOR_SKILLSET["prompts"],
        tools=DEFAULT_ICEBERG_ADVISOR_SKILLSET["tools"],
        is_active=True,
    )


def register_skillset(session: Session, skillset: Skillset) -> Skillset:
    """Insert or update a skillset version.

    If ``skillset.is_active`` is True, all other versions of the same name
    are deactivated first so there's exactly one active version at a time.
    """
    existing = session.scalar(
        select(SkillsetRow).where(
            SkillsetRow.name == skillset.name,
            SkillsetRow.version == skillset.version,
        )
    )

    if skillset.is_active:
        session.execute(
            update(SkillsetRow)
            .where(SkillsetRow.name == skillset.name)
            .values(is_active=False)
        )

    if existing is not None:
        existing.prompts = dict(skillset.prompts or {})
        existing.tools = list(skillset.tools or [])
        existing.is_active = bool(skillset.is_active)
        existing.metrics = dict(skillset.metrics or {})
        session.flush()
        row = existing
    else:
        row = SkillsetRow(
            name=skillset.name,
            version=skillset.version,
            prompts=dict(skillset.prompts or {}),
            tools=list(skillset.tools or []),
            is_active=bool(skillset.is_active),
            metrics=dict(skillset.metrics or {}),
        )
        session.add(row)
        session.flush()

    session.commit()
    return Skillset.from_row(row)


def set_active_skillset(session: Session, name: str, version: str) -> Skillset:
    """Activate a specific (name, version). Deactivates all others under name."""
    target = session.scalar(
        select(SkillsetRow).where(
            SkillsetRow.name == name, SkillsetRow.version == version
        )
    )
    if target is None:
        raise LookupError(f"Skillset {name}@{version} not found")

    session.execute(
        update(SkillsetRow)
        .where(SkillsetRow.name == name)
        .values(is_active=False)
    )
    target.is_active = True
    session.flush()
    session.commit()
    return Skillset.from_row(target)


def list_skillsets(session: Session) -> list[Skillset]:
    """Return every skillset version, newest first."""
    _ensure_builtin_default(session)
    session.commit()
    rows = session.scalars(
        select(SkillsetRow).order_by(SkillsetRow.created_at.desc())
    ).all()
    return [Skillset.from_row(row) for row in rows]


def record_outcome(
    session: Session,
    skillset_id: uuid.UUID | str,
    *,
    prompt_name: str,
    recommendation: str,
    decision: str,
    score: float | None = None,
    table_id: uuid.UUID | str | None = None,
) -> SkillsetOutcome:
    """Record a single recommendation outcome and roll up metrics."""
    if decision not in {"accepted", "rejected", "pending"}:
        raise ValueError(f"Invalid decision: {decision!r}")

    skillset_uuid = uuid.UUID(str(skillset_id))
    table_uuid = uuid.UUID(str(table_id)) if table_id is not None else None

    outcome = SkillsetOutcome(
        skillset_id=skillset_uuid,
        prompt_name=prompt_name,
        recommendation=recommendation,
        decision=decision,
        score=score,
        table_id=table_uuid,
    )
    session.add(outcome)
    session.flush()

    row = session.get(SkillsetRow, skillset_uuid)
    if row is not None:
        metrics = dict(row.metrics or {})
        accept = int(metrics.get("accept_count", 0))
        reject = int(metrics.get("reject_count", 0))
        samples = int(metrics.get("sample_count", 0))
        score_sum = float(metrics.get("score_sum", 0.0))
        if decision == "accepted":
            accept += 1
        elif decision == "rejected":
            reject += 1
        samples += 1
        if score is not None:
            score_sum += float(score)
            metrics["avg_score"] = round(score_sum / max(samples, 1), 4)
        metrics["accept_count"] = accept
        metrics["reject_count"] = reject
        metrics["sample_count"] = samples
        denom = max(accept + reject, 1)
        metrics["acceptance_rate"] = round(accept / denom, 4)
        row.metrics = metrics

    session.commit()
    return outcome


__all__ = [
    "DEFAULT_ICEBERG_ADVISOR_SKILLSET",
    "Skillset",
    "list_skillsets",
    "load_skillset",
    "record_outcome",
    "register_skillset",
    "set_active_skillset",
]