"""Real-dollar circuit breaker on the cryo EKS namespace's own daily spend.

Distinct from cryo.reporting.cost_signals (which tracks Cryo's estimate of the *fleet's*
S3 storage cost, derived from TableCostHistory) and from
cryo.governor.native_admit._FleetAdmissionRateLimiter (which caps admission *volume* as a
proxy, with no visibility into what that volume actually costs). This module checks the
real bill: the used+unused cost Grafana's own "Namespace Cost" dashboard reports for the
cryo namespace itself (worker/api/governor/scheduler/scanner-worker pods, requested vs.
actually-used CPU/memory, per AWS Cost and Usage Report data in Athena), and halts new
admissions via the existing global-pause mechanism (MaintenanceControlState) if today's
spend crosses the configured ceiling.

Built 2026-08-12 at the user's explicit request for a hard $100/day ceiling on the cryo
namespace (used + unused). Real data pulled that day: the namespace averaged $210.82/day
over the trailing 14 days, over $100 on 8 of 14 -- driven mostly by idle/unused capacity
(fixed replica counts running below load), not just the 2026-08-11 incident spike. This
is the enforcement half; cryo.governor.native_admit's fleet-wide admission rate cap and
the scanner-worker autoscaler (deploy/helm/cryo values) are the prevention half -- this
module is the backstop for whatever those don't catch, checked against the real bill
instead of a proxy for it.

Uses the exact query shape reverse-engineered from Grafana dashboard uid
de05f8e7cjpj4ea's "${namespace} Namespace Total Cost" / "Unused Cost" panels (Athena CUR
datasource fe0bimj2kv9xca) -- see [[grafana-cost-query-patterns]] memory note for the
general pattern this was derived from.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.config import get_settings
from cryo.db.models import MaintenanceControlState
from cryo.db.repositories.intents import set_pause_state

logger = logging.getLogger(__name__)

_GRAFANA_QUERY_PATH = "/api/ds/query"
_ATHENA_DATASOURCE_UID = "fe0bimj2kv9xca"
_CUR_CONNECTION_ARGS = {
    "catalog": "__default",
    "database": "__default",
    "region": "__default",
    "resultReuseEnabled": False,
    "resultReuseMaxAgeInMinutes": 60,
}
_TODAYS_COST_SQL = """
select
    sum(split_line_item_split_cost) AS used_cost,
    sum(split_line_item_unused_cost) AS unused_cost
from cur
where date_format(line_item_usage_start_date, '%Y-%m-%d') = '{day}'
  and resource_tags_aws_eks_cluster_name = '{cluster}'
  and resource_tags_aws_eks_namespace = '{namespace}'
"""

_CEILING_PAUSE_REASON_PREFIX = "cost_ceiling_breach:"


@dataclass(frozen=True, slots=True)
class NamespaceCostCheck:
    day: date
    used_cost_usd: float
    unused_cost_usd: float
    ceiling_usd: float

    @property
    def total_cost_usd(self) -> float:
        return self.used_cost_usd + self.unused_cost_usd

    @property
    def over_ceiling(self) -> bool:
        return self.total_cost_usd > self.ceiling_usd


def fetch_todays_namespace_cost(
    *,
    grafana_url: str,
    grafana_token: str,
    cluster_name: str,
    namespace: str,
    ceiling_usd: float,
    day: date | None = None,
    timeout_seconds: float = 15.0,
) -> NamespaceCostCheck:
    """Query Grafana's Athena CUR datasource for today's real used+unused cost.

    CUR data lags 24-48h (see [[grafana-cost-query-patterns]]) -- querying "today" can
    legitimately return zero/partial rows before AWS has finished billing the day. That's
    not treated as an error here (a day with no data yet is not a day over budget); the
    caller decides how to interpret a zero-cost day for a day that's still in progress.
    """
    target_day = day or datetime.now(UTC).date()
    sql = _TODAYS_COST_SQL.format(
        day=target_day.isoformat(), cluster=cluster_name, namespace=namespace
    )
    payload = {
        "queries": [
            {
                "refId": "A",
                "datasource": {"uid": _ATHENA_DATASOURCE_UID, "type": "grafana-athena-datasource"},
                "format": 1,
                "table": "cur",
                "connectionArgs": _CUR_CONNECTION_ARGS,
                "rawSQL": sql,
            }
        ]
    }
    response = httpx.post(
        f"{grafana_url.rstrip('/')}{_GRAFANA_QUERY_PATH}",
        headers={"Authorization": f"Bearer {grafana_token}"},
        json=payload,
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    body = response.json()

    result = body.get("results", {}).get("A", {})
    if "error" in result:
        raise RuntimeError(f"Grafana query error: {result['error']}")

    frames = result.get("frames") or []
    used = 0.0
    unused = 0.0
    if frames:
        fields = frames[0].get("schema", {}).get("fields", [])
        values = frames[0].get("data", {}).get("values", [])
        field_names = [f.get("name") for f in fields]
        row = {
            name: (vals[0] if vals else None)
            for name, vals in zip(field_names, values, strict=False)
        }
        used = float(row.get("used_cost") or 0.0)
        unused = float(row.get("unused_cost") or 0.0)

    return NamespaceCostCheck(
        day=target_day,
        used_cost_usd=used,
        unused_cost_usd=unused,
        ceiling_usd=ceiling_usd,
    )


def enforce_cost_ceiling(
    session: Session,
    *,
    grafana_url: str,
    grafana_token: str,
) -> NamespaceCostCheck | None:
    """Check today's real cryo-namespace spend; pause fleet-wide admission if it's over
    the configured ceiling, resume if a previous breach has since cleared.

    Global pause/resume reuses the existing MaintenanceControlState mechanism (same one
    an operator's manual "pause all maintenance" button writes to) rather than a parallel
    kill-switch -- one enforcement path, already tested, already has an audit trail and a
    UI. Only ever touches the pause row this module itself owns (reason prefixed
    cost_ceiling_breach:) -- never resumes a pause an operator set manually for an
    unrelated reason, and never overwrites an operator's manual pause with its own state.
    """
    settings = get_settings()
    if not settings.cost_ceiling_enabled:
        return None

    try:
        check = fetch_todays_namespace_cost(
            grafana_url=grafana_url,
            grafana_token=grafana_token,
            cluster_name=settings.cost_ceiling_cluster_name,
            namespace=settings.cost_ceiling_namespace,
            ceiling_usd=settings.cost_ceiling_daily_usd,
        )
    except Exception:
        logger.warning("Cost ceiling check failed to fetch Grafana data", exc_info=True)
        return None

    existing = session.scalar(
        select(MaintenanceControlState).where(
            MaintenanceControlState.scope_type == "global",
            MaintenanceControlState.scope_id == "global",
        )
    )
    # Only this module's own prior breach is ours to clear -- an operator-set pause has
    # no reason, or a different one, and must never be silently resumed by a cost check.
    owned_by_us = (
        existing is not None
        and existing.paused
        and (existing.reason or "").startswith(_CEILING_PAUSE_REASON_PREFIX)
    )

    if check.over_ceiling:
        if not (existing is not None and existing.paused):
            logger.warning(
                "Cost ceiling breached: cryo namespace $%.2f today (used $%.2f + unused $%.2f) "
                "over $%.2f ceiling -- pausing fleet-wide admission",
                check.total_cost_usd,
                check.used_cost_usd,
                check.unused_cost_usd,
                check.ceiling_usd,
            )
            set_pause_state(
                session,
                scope_type="global",
                scope_id="global",
                paused=True,
                changed_by="cost_ceiling_checker",
                reason=(
                    f"{_CEILING_PAUSE_REASON_PREFIX} {check.day.isoformat()} spend "
                    f"${check.total_cost_usd:.2f} exceeded ${check.ceiling_usd:.2f} ceiling "
                    f"(used ${check.used_cost_usd:.2f} + unused ${check.unused_cost_usd:.2f})"
                ),
            )
            session.commit()
        # else: already paused by us (or by an operator) -- nothing new to do, don't
        # bump the audit log / generation counter on every tick while still over budget.
    elif owned_by_us:
        logger.info(
            "Cost ceiling check: cryo namespace back under $%.2f ceiling ($%.2f today) -- "
            "resuming fleet-wide admission",
            check.ceiling_usd,
            check.total_cost_usd,
        )
        set_pause_state(
            session,
            scope_type="global",
            scope_id="global",
            paused=False,
            changed_by="cost_ceiling_checker",
            reason=(
                f"cost_ceiling_cleared: {check.day.isoformat()} spend "
                f"${check.total_cost_usd:.2f} under ceiling"
            ),
        )
        session.commit()

    return check
