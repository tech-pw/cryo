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

# Same shape as _TODAYS_COST_SQL, grouped by day over a range instead of pinned to one date --
# used by fetch_namespace_cost_range (cryo.cost.model's Trino $/byte price derivation reads a
# trailing window, not just today) rather than duplicating the query with a different WHERE.
_COST_RANGE_SQL = """
select
    date_format(line_item_usage_start_date, '%Y-%m-%d') as day,
    sum(split_line_item_split_cost) AS used_cost,
    sum(split_line_item_unused_cost) AS unused_cost
from cur
where date_format(line_item_usage_start_date, '%Y-%m-%d') >= '{since}'
  and date_format(line_item_usage_start_date, '%Y-%m-%d') <= '{until}'
  and resource_tags_aws_eks_cluster_name = '{cluster}'
  and resource_tags_aws_eks_namespace = '{namespace}'
GROUP BY date_format(line_item_usage_start_date, '%Y-%m-%d')
ORDER BY day
"""

_CEILING_PAUSE_REASON_PREFIX = "cost_ceiling_breach:"


@dataclass(frozen=True, slots=True)
class NamespaceCostCheck:
    day: date
    used_cost_usd: float
    unused_cost_usd: float
    ceiling_usd: float
    # 2026-09-04: graduated response, see enforce_cost_ceiling's own docstring for the
    # incident this fixes. A day within [ceiling, ceiling * (1 + tolerance_pct)] is over
    # the strict ceiling but not a real breach worth halting the whole fleet for.
    tolerance_pct: float = 0.0

    @property
    def total_cost_usd(self) -> float:
        return self.used_cost_usd + self.unused_cost_usd

    @property
    def over_ceiling(self) -> bool:
        return self.total_cost_usd > self.ceiling_usd

    @property
    def severely_over_ceiling(self) -> bool:
        """True only once spend clears the tolerance band above the ceiling -- this, not
        over_ceiling, is what should ever trigger a fleet-wide pause. See
        enforce_cost_ceiling's docstring."""
        return self.total_cost_usd > self.ceiling_usd * (1 + self.tolerance_pct)


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


@dataclass(frozen=True, slots=True)
class NamespaceDayCost:
    day: date
    used_cost_usd: float
    unused_cost_usd: float

    @property
    def total_cost_usd(self) -> float:
        return self.used_cost_usd + self.unused_cost_usd


def fetch_namespace_cost_range(
    *,
    grafana_url: str,
    grafana_token: str,
    cluster_name: str,
    namespace: str,
    since: date,
    until: date | None = None,
    timeout_seconds: float = 15.0,
) -> list[NamespaceDayCost]:
    """Same Athena CUR datasource/query shape as fetch_todays_namespace_cost, grouped by day
    over [since, until] instead of pinned to one date -- for callers that need a trailing
    window (e.g. cryo.cost.model's Trino $/byte price derivation, which needs several days of
    namespace cost to correlate against QueryCostAttributionDaily's bytes-scanned rollup, not
    just today). Days with no CUR rows yet (lag 24-48h, see fetch_todays_namespace_cost's
    docstring) are simply absent from the returned list, not zero-filled -- the caller decides
    how to treat a missing day rather than this function guessing "no data" vs "genuinely $0".
    """
    target_until = until or datetime.now(UTC).date()
    sql = _COST_RANGE_SQL.format(
        since=since.isoformat(),
        until=target_until.isoformat(),
        cluster=cluster_name,
        namespace=namespace,
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
    if not frames:
        return []

    fields = frames[0].get("schema", {}).get("fields", [])
    values = frames[0].get("data", {}).get("values", [])
    field_names = [f.get("name") for f in fields]
    columns = dict(zip(field_names, values, strict=False))
    days = columns.get("day") or []
    used_costs = columns.get("used_cost") or []
    unused_costs = columns.get("unused_cost") or []

    results: list[NamespaceDayCost] = []
    for i, raw_day in enumerate(days):
        if raw_day is None:
            continue
        parsed_day = raw_day if isinstance(raw_day, date) else date.fromisoformat(str(raw_day))
        used_val = used_costs[i] if i < len(used_costs) else None
        unused_val = unused_costs[i] if i < len(unused_costs) else None
        results.append(
            NamespaceDayCost(
                day=parsed_day,
                used_cost_usd=float(used_val) if used_val is not None else 0.0,
                unused_cost_usd=float(unused_val) if unused_val is not None else 0.0,
            )
        )
    return results


def fetch_latest_billed_namespace_cost(
    *,
    grafana_url: str,
    grafana_token: str,
    cluster_name: str,
    namespace: str,
    ceiling_usd: float,
    lookback_days: int = 4,
    timeout_seconds: float = 15.0,
    tolerance_pct: float = 0.0,
) -> NamespaceCostCheck | None:
    """The real fix for PDAE-8541: return the most recent day with committed CUR rows,
    not literally "today".

    fetch_todays_namespace_cost() (kept below, unchanged, for callers that genuinely want
    "today" e.g. a live dashboard) queries `current_date` -- but AWS CUR data lags 24-48h,
    so "today" structurally has zero committed rows for essentially its entire duration.
    Confirmed live, 2026-08-18: every enforce_cost_ceiling() tick logged
    "$0.00 today (used $0.00 + unused $0.00) vs $100.00 ceiling" while real trailing spend
    was genuinely ~$209/day (independently confirmed via a fresh Grafana pull) -- the
    ceiling was structurally incapable of ever breaching, not merely miscalibrated.

    Pulls a short trailing window (default 4 days -- comfortably covers the 24-48h lag even
    on a slow billing day) via fetch_namespace_cost_range and returns a NamespaceCostCheck
    for the single most recent day that actually has rows. Returns None only if the entire
    lookback window is still empty (a real Grafana/CUR outage, not just today's normal lag)
    -- callers must treat that as "can't evaluate right now", never as "$0, under budget".
    """
    until = datetime.now(UTC).date()
    since = until.fromordinal(until.toordinal() - lookback_days)
    days = fetch_namespace_cost_range(
        grafana_url=grafana_url,
        grafana_token=grafana_token,
        cluster_name=cluster_name,
        namespace=namespace,
        since=since,
        until=until,
        timeout_seconds=timeout_seconds,
    )
    if not days:
        return None
    latest = max(days, key=lambda d: d.day)
    return NamespaceCostCheck(
        day=latest.day,
        used_cost_usd=latest.used_cost_usd,
        unused_cost_usd=latest.unused_cost_usd,
        ceiling_usd=ceiling_usd,
        tolerance_pct=tolerance_pct,
    )


def enforce_cost_ceiling(
    session: Session,
    *,
    grafana_url: str,
    grafana_token: str,
) -> NamespaceCostCheck | None:
    """Check the most recent fully-billed day's real cryo-namespace spend; pause fleet-wide
    admission if it's over the configured ceiling, resume if a previous breach has since
    cleared.

    Global pause/resume reuses the existing MaintenanceControlState mechanism (same one
    an operator's manual "pause all maintenance" button writes to) rather than a parallel
    kill-switch -- one enforcement path, already tested, already has an audit trail and a
    UI. Only ever touches the pause row this module itself owns (reason prefixed
    cost_ceiling_breach:) -- never resumes a pause an operator set manually for an
    unrelated reason, and never overwrites an operator's manual pause with its own state.

    PDAE-8541: uses fetch_latest_billed_namespace_cost (trailing window, most recent day
    with real data), not fetch_todays_namespace_cost -- the latter compares against
    `current_date`, which structurally has zero committed CUR rows for essentially its
    entire duration (AWS bills 24-48h late), so the ceiling could never actually breach.

    2026-09-04 fix: previously paused on *any* overage, however small -- confirmed live,
    a $1.06 overage on a $100 ceiling ($101.06, 1.06% over) triggered a full fleet-wide
    admission halt that sat for 4+ hours until a human noticed and manually resumed it,
    during which the queue backed up to 13k+ jobs and vitals read 0/100 CRITICAL. The
    ceiling itself was also stale: calibrated 2026-08-12 against a namespace that was
    mostly idle due to since-fixed governor/scheduler bugs -- real spend at healthy
    throughput (2026-08-31 onward, once those fixes shipped and the fleet actually started
    processing its real backlog) runs ~$150-195/day, not the ~$0-50/day baseline the
    original $100 ceiling assumed.

    Now graduated: only a breach beyond cost_ceiling_tolerance_pct above the ceiling (see
    that field's own docstring) triggers the fleet-wide pause -- a purely cosmetic overage
    logs a warning and lets admission continue. This is still a real circuit breaker for
    genuine runaway spend, not a removal of the safety net; it just stops treating a
    rounding-error overage as an emergency requiring the same response as a 5x spike.
    """
    settings = get_settings()
    if not settings.cost_ceiling_enabled:
        return None

    try:
        check = fetch_latest_billed_namespace_cost(
            grafana_url=grafana_url,
            grafana_token=grafana_token,
            cluster_name=settings.cost_ceiling_cluster_name,
            namespace=settings.cost_ceiling_namespace,
            ceiling_usd=settings.cost_ceiling_daily_usd,
            tolerance_pct=settings.cost_ceiling_tolerance_pct,
        )
    except Exception:
        logger.warning("Cost ceiling check failed to fetch Grafana data", exc_info=True)
        return None

    if check is None:
        logger.warning(
            "Cost ceiling check found no committed CUR rows in the trailing lookback "
            "window -- skipping this tick rather than treating it as $0/under budget"
        )
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

    if check.over_ceiling and not check.severely_over_ceiling:
        # Cosmetic overage: within tolerance of the ceiling. Log it (visible in the
        # namespace_unused_cost vitals check regardless) but never pause for this alone,
        # and if a prior severe breach has cooled down into just this tolerance band,
        # resume -- staying paused for a day that's no longer a real breach has the same
        # cost as never having graduated the response at all.
        logger.info(
            "Cost ceiling: cryo namespace $%.2f today is over the $%.2f ceiling but within "
            "the %.0f%% tolerance band -- not pausing",
            check.total_cost_usd,
            check.ceiling_usd,
            settings.cost_ceiling_tolerance_pct * 100,
        )
        if owned_by_us:
            logger.info(
                "Cost ceiling: prior breach has cooled into the tolerance band -- resuming "
                "fleet-wide admission"
            )
            set_pause_state(
                session,
                scope_type="global",
                scope_id="global",
                paused=False,
                changed_by="cost_ceiling_checker",
                reason=(
                    f"cost_ceiling_cleared: {check.day.isoformat()} spend "
                    f"${check.total_cost_usd:.2f} back within tolerance of "
                    f"${check.ceiling_usd:.2f} ceiling"
                ),
            )
            session.commit()
    elif check.severely_over_ceiling:
        if not (existing is not None and existing.paused):
            logger.warning(
                "Cost ceiling breached: cryo namespace $%.2f today (used $%.2f + unused $%.2f) "
                "exceeds $%.2f ceiling by more than the %.0f%% tolerance band -- pausing "
                "fleet-wide admission",
                check.total_cost_usd,
                check.used_cost_usd,
                check.unused_cost_usd,
                check.ceiling_usd,
                settings.cost_ceiling_tolerance_pct * 100,
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
