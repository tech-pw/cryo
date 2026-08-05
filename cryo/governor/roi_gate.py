from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy.orm import Session

from cryo.config import get_settings
from cryo.db.models import TableCost, TableStats
from cryo.governor.admission import AdmissionJob
from cryo.planner.benefit_score import BenefitCostBreakdown, enhanced_bcr_from_table


class GateAction(StrEnum):
    PROCEED = "proceed"
    SKIP = "skip"
    DEFER = "defer"


@dataclass(frozen=True)
class GateDecision:
    action: GateAction
    reason: str
    bcr: float
    threshold: float
    shadow: bool
    breakdown: BenefitCostBreakdown


class ROIGate:
    def __init__(self) -> None:
        self._settings = get_settings()

    def decide(self, session: Session, job: AdmissionJob, *, record: bool = True) -> GateDecision:
        table_uuid = _parse_uuid(job.table_id)
        if table_uuid is None:
            breakdown = _zero_breakdown()
            return GateDecision(
                action=GateAction.PROCEED,
                reason="invalid_table_id",
                bcr=breakdown.bcr,
                threshold=self._settings.roi_min_bcr,
                shadow=self._settings.roi_gate_shadow_mode,
                breakdown=breakdown,
            )

        stats = session.get(TableStats, table_uuid)
        if stats is None:
            breakdown = _zero_breakdown()
            return GateDecision(
                action=GateAction.PROCEED,
                reason="missing_table_stats",
                bcr=breakdown.bcr,
                threshold=self._settings.roi_min_bcr,
                shadow=self._settings.roi_gate_shadow_mode,
                breakdown=breakdown,
            )

        cost = session.get(TableCost, table_uuid)
        stats_payload = {
            "query_count_30d": stats.query_count_30d,
            "small_files_count": stats.small_files_count,
            "delete_file_pct": stats.delete_file_pct,
            "total_size_gb": stats.total_size_gb,
            "projected_savings_usd_month": stats.projected_savings_usd_month,
            "query_request_savings_usd_month": cost.query_request_savings_usd_month
            if cost
            else 0.0,
        }
        breakdown = enhanced_bcr_from_table(
            stats_payload,
            projected_storage_savings_usd_month=(
                cost.projected_savings_usd_month
                if cost and cost.projected_savings_usd_month is not None
                else None
            ),
            query_request_savings_usd_month=(
                cost.query_request_savings_usd_month
                if cost and cost.query_request_savings_usd_month is not None
                else None
            ),
        )

        is_streaming = bool((stats.raw or {}).get("is_streaming")) or stats.detection_tier == "hot"
        threshold = (
            self._settings.roi_min_bcr_streaming if is_streaming else self._settings.roi_min_bcr
        )
        bcr = breakdown.bcr
        shadow_mode = bool(self._settings.roi_gate_shadow_mode)
        if shadow_mode and _shadow_ready(stats, self._settings.roi_shadow_min_samples):
            shadow_mode = False

        if bcr >= threshold:
            action = GateAction.PROCEED
            reason = "bcr_above_threshold"
        elif bcr < threshold * 0.5:
            action = GateAction.SKIP
            reason = "bcr_far_below_threshold"
        else:
            action = GateAction.DEFER
            reason = "bcr_below_threshold"

        decision = GateDecision(
            action=action,
            reason=reason,
            bcr=round(bcr, 6),
            threshold=threshold,
            shadow=shadow_mode,
            breakdown=breakdown,
        )
        if record:
            _persist_shadow_observation(stats, decision)
        return decision


def _parse_uuid(raw: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError):
        return None


def _zero_breakdown() -> BenefitCostBreakdown:
    return BenefitCostBreakdown(
        latency_benefit_usd_month=0.0,
        storage_benefit_usd_month=0.0,
        request_benefit_usd_month=0.0,
        opportunity_benefit_usd_month=0.0,
        rewrite_compute_cost_usd_month=0.0,
        write_amplification_cost_usd_month=0.0,
        opportunity_cost_usd_month=0.0,
    )


def _persist_shadow_observation(stats: TableStats, decision: GateDecision) -> None:
    raw: dict[str, Any] = dict(stats.raw or {})
    smart = dict(raw.get("smart_maintenance") or {})
    history = smart.get("roi_decisions")
    events = list(history) if isinstance(history, list) else []
    events.append(
        {
            "ts": datetime.now(UTC).isoformat(),
            "action": decision.action.value,
            "reason": decision.reason,
            "bcr": decision.bcr,
            "threshold": decision.threshold,
            "shadow": decision.shadow,
            "benefit_usd_month": decision.breakdown.total_benefit_usd_month,
            "cost_usd_month": decision.breakdown.total_cost_usd_month,
        }
    )
    smart["roi_decisions"] = events[-200:]
    raw["smart_maintenance"] = smart
    stats.raw = raw


def _shadow_ready(stats: TableStats, min_samples: int) -> bool:
    raw: dict[str, Any] = dict(stats.raw or {})
    smart = dict(raw.get("smart_maintenance") or {})
    history = smart.get("roi_decisions")
    if not isinstance(history, list):
        return False
    return len(history) >= max(10, min_samples)
