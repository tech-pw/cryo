"""Evidence-first anomaly root-cause brief generation."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from cryo.ai.runtime import LLMInvokeContext, complete_with_budget_and_observation
from cryo.db.models import AuditLog, QueryPattern, Table, TableStatsHistory


@dataclass(slots=True)
class RootCauseHypothesis:
    title: str
    confidence: float
    because: str


@dataclass(slots=True)
class RootCauseBrief:
    table_id: uuid.UUID
    evidence: list[str]
    hypotheses: list[RootCauseHypothesis]
    used_llm: bool
    observation_id: uuid.UUID | None = None


def _deterministic_hypotheses(evidence: list[str]) -> list[RootCauseHypothesis]:
    text = " ".join(evidence).lower()
    out: list[RootCauseHypothesis] = []
    if "small files" in text and ("increased" in text or "jumped" in text):
        out.append(
            RootCauseHypothesis(
                title="Writer produced many small files",
                confidence=0.78,
                because="Small-file count rose sharply between recent scans.",
            )
        )
    if "schema" in text and "change" in text:
        out.append(
            RootCauseHypothesis(
                title="Recent schema evolution impacted layout",
                confidence=0.64,
                because="Schema-change events align with the anomaly window.",
            )
        )
    if "maintenance gap" in text or "days since maintenance" in text:
        out.append(
            RootCauseHypothesis(
                title="Maintenance backlog accumulation",
                confidence=0.74,
                because="Table exceeded normal maintenance cadence during anomaly period.",
            )
        )
    if not out:
        out.append(
            RootCauseHypothesis(
                title="Insufficient evidence for a strong root cause",
                confidence=0.4,
                because="Available telemetry does not isolate a single dominant cause.",
            )
        )
    return out[:3]


def build_evidence(session: Session, table_id: uuid.UUID) -> list[str]:
    evidence: list[str] = []
    history = list(
        session.scalars(
            select(TableStatsHistory)
            .where(TableStatsHistory.table_id == table_id)
            .order_by(desc(TableStatsHistory.scan_at))
            .limit(3)
        )
    )
    if len(history) >= 2:
        newest, previous = history[0], history[1]
        delta_small = int(newest.small_files_count or 0) - int(previous.small_files_count or 0)
        delta_score = float(newest.overall_score or 0) - float(previous.overall_score or 0)
        evidence.append(
            f"Small files changed by {delta_small:+d}; score changed by {delta_score:+.1f} between latest scans."
        )
        raw_new = dict(newest.raw or {})
        raw_old = dict(previous.raw or {})
        if raw_new.get("schema_hash") and raw_new.get("schema_hash") != raw_old.get("schema_hash"):
            evidence.append("Schema change detected in latest scan window.")

    audits = list(
        session.scalars(
            select(AuditLog)
            .where(AuditLog.table_id == table_id)
            .order_by(desc(AuditLog.ts))
            .limit(5)
        )
    )
    if audits:
        last_ok = next((a for a in audits if (a.status or "").upper() == "OK"), None)
        if last_ok is not None:
            hours = (history[0].scan_at - last_ok.ts).total_seconds() / 3600 if history else None
            if hours is not None:
                evidence.append(f"Last successful maintenance was {hours:.1f}h before latest scan.")
        last_fail = next((a for a in audits if (a.status or "").upper() != "OK"), None)
        if last_fail is not None:
            evidence.append(f"Recent maintenance failure recorded: action={last_fail.action}.")

    qps = list(
        session.scalars(
            select(QueryPattern)
            .where(QueryPattern.table_id == table_id)
            .order_by(desc(QueryPattern.filter_count))
            .limit(3)
        )
    )
    if qps:
        top = qps[0]
        evidence.append(
            f"Column '{top.column_name}' is the most frequently filtered "
            f"({top.filter_count} filters, {top.join_count} joins) in recent query patterns."
        )

    table = session.get(Table, table_id)
    if table is not None:
        siblings = list(
            session.scalars(
                select(Table.id)
                .where(
                    Table.schema_name == table.schema_name,
                    Table.id != table_id,
                    Table.is_active.is_(True),
                )
                .limit(10)
            )
        )
        evidence.append(f"{len(siblings)} sibling tables found in schema {table.schema_name}.")
    return evidence


async def compose_root_cause_brief(session: Session, table_id: uuid.UUID) -> RootCauseBrief:
    evidence = build_evidence(session, table_id)
    deterministic = _deterministic_hypotheses(evidence)
    if not evidence:
        return RootCauseBrief(
            table_id=table_id,
            evidence=[],
            hypotheses=deterministic,
            used_llm=False,
        )

    prompt = (
        "Given deterministic evidence facts, produce up to 3 ranked hypotheses as JSON.\n"
        "Schema: {\"hypotheses\":[{\"title\":str,\"confidence\":0..1,\"because\":str}]}.\n"
        "Never invent facts; because must cite only provided evidence.\n"
        f"Evidence:\n- " + "\n- ".join(evidence[:10])
    )
    response, observation = await complete_with_budget_and_observation(
        session,
        prompt,
        invoke=LLMInvokeContext(
            feature="anomaly_root_cause_brief",
            prompt_name="anomaly_root_cause_brief",
            table_id=table_id,
        ),
        max_tokens=260,
    )
    if response is None:
        return RootCauseBrief(
            table_id=table_id,
            evidence=evidence,
            hypotheses=deterministic,
            used_llm=False,
            observation_id=observation.id,
        )

    parsed: dict[str, Any] | None = None
    try:
        parsed = json.loads(response.content)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", response.content)
        if m:
            try:
                parsed = json.loads(m.group(0))
            except json.JSONDecodeError:
                parsed = None
    payload = parsed.get("hypotheses") if isinstance(parsed, dict) else None
    if not isinstance(payload, list):
        return RootCauseBrief(
            table_id=table_id,
            evidence=evidence,
            hypotheses=deterministic,
            used_llm=False,
            observation_id=observation.id,
        )
    hypotheses: list[RootCauseHypothesis] = []
    for item in payload[:3]:
        if not isinstance(item, dict):
            continue
        hypotheses.append(
            RootCauseHypothesis(
                title=str(item.get("title") or "Hypothesis"),
                confidence=max(0.0, min(float(item.get("confidence") or 0.0), 1.0)),
                because=str(item.get("because") or ""),
            )
        )
    if not hypotheses:
        hypotheses = deterministic
    return RootCauseBrief(
        table_id=table_id,
        evidence=evidence,
        hypotheses=hypotheses,
        used_llm=True,
        observation_id=observation.id,
    )
