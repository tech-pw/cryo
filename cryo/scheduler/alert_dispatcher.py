"""Detect SLA and data-quality breaches per table and notify the owner + subscribers.

The SLA thresholds come from ``sla_from_guardrails`` -- the same resolver the
``/sla-status`` dashboard endpoint uses -- and the data-quality signals come from
``profile_table_quality`` -- the same function backing the Data Quality tab's
null-rate spikes, value-range shifts, and schema drift. So an alert can never claim
something the dashboards themselves don't also show, without this module having to
call into the HTTP-route layer directly (which assumes a request-scoped principal
and a Postgres-native tz-aware datetime round-trip neither of which hold here).

Only tables with an owner or at least one subscriber are checked (there's no one to
tell otherwise), and a table only re-alerts after ``table_alert_cooldown_hours`` have
passed since its last dispatch, so a sustained breach doesn't spam an inbox every tick.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from cryo.api.sla import sla_from_guardrails
from cryo.config import get_settings
from cryo.db.model_utils import qualified_table_name
from cryo.db.models import AuditLog, QueryLog, Table, TableAlertDispatch, TableAlertSubscription
from cryo.db.repositories import presets as preset_repo

logger = logging.getLogger(__name__)


@dataclass
class TableBreach:
    table_id: str
    table_name: str
    reasons: list[str] = field(default_factory=list)


def _as_utc(value: datetime) -> datetime:
    """Normalize a datetime to tz-aware UTC.

    SQLite (used in tests) round-trips ``TIMESTAMP(timezone=True)`` columns as naive
    datetimes even when stored aware; Postgres (production) preserves tz-awareness.
    Comparing against ``datetime.now(UTC)`` needs both sides aware regardless of backend.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _raw_datetime(raw: object, *keys: str) -> datetime | None:
    data = raw if isinstance(raw, dict) else {}
    for key in keys:
        value = data.get(key)
        if isinstance(value, datetime):
            return _as_utc(value)
        if isinstance(value, str):
            try:
                return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
            except ValueError:
                continue
    return None


def _sla_breach_reasons(session: Session, table: Table) -> list[str]:
    stats = table.stats
    if stats is None:
        return []

    now = datetime.now(UTC)
    effective = preset_repo.resolve_effective_preset(session, table)
    guardrails = dict(effective.preset.guardrails) if effective and effective.preset else {}
    sla = sla_from_guardrails(guardrails)

    last_commit = _raw_datetime(stats.raw, "last_snapshot_committed_at")
    freshness_hours = (now - (last_commit or _as_utc(stats.scan_at))).total_seconds() / 3600

    last_maintenance = session.scalar(
        select(func.max(AuditLog.ts)).where(
            AuditLog.table_id == table.id, AuditLog.status == "succeeded"
        )
    )
    maintenance_days = (
        (now - _as_utc(last_maintenance)).total_seconds() / 86400 if last_maintenance else None
    )

    p95_latency_ms = None
    bind = session.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        # percentile_cont is a Postgres-only ordered-set aggregate (absent under the SQLite
        # test harness) -- skip the check there rather than let a dialect mismatch sink the
        # whole breach check.
        p95_latency_ms = session.scalar(
            select(func.percentile_cont(0.95).within_group(QueryLog.duration_ms)).where(
                QueryLog.table_id == table.id, QueryLog.executed_at >= now - timedelta(days=1)
            )
        )

    reasons: list[str] = []
    if freshness_hours > sla.freshness_hours:
        reasons.append(
            f"Freshness breached: {freshness_hours:.1f}h since last commit "
            f"(SLA {sla.freshness_hours}h)"
        )
    if maintenance_days is not None and maintenance_days > sla.maintenance_days:
        reasons.append(
            f"Maintenance overdue: {maintenance_days:.1f}d since last run "
            f"(SLA {sla.maintenance_days}d)"
        )
    if p95_latency_ms is not None and p95_latency_ms > sla.p95_latency_ms:
        reasons.append(
            f"P95 query latency breached: {p95_latency_ms:.0f}ms (SLA {sla.p95_latency_ms}ms)"
        )
    return reasons


def _quality_breach_reasons(session: Session, table_id: object) -> list[str]:
    from cryo.quality.profiler import profile_table_quality

    try:
        # profile_table_quality's `table_id` param is typed `str` but must be passed the actual
        # UUID object here, not `str(table_id)` -- SQLAlchemy's UUID(as_uuid=True) bind processor
        # (exercised by the SQLite test harness; Postgres/psycopg tolerates either) needs a real
        # uuid.UUID to encode, and rejects a plain string.
        profile = profile_table_quality(session, table_id, lookback_days=30)  # type: ignore[arg-type]
    except ValueError:
        # No stats yet for this table -- nothing to check.
        return []

    reasons: list[str] = []
    spiking = [m.column_name for m in profile.column_metrics if m.null_rate_spike]
    if spiking:
        reasons.append(f"Null-rate spike on {len(spiking)} column(s): {', '.join(spiking[:5])}")
    shifted = [m.column_name for m in profile.column_metrics if m.value_range_changed]
    if shifted:
        reasons.append(f"Value-range shift on {len(shifted)} column(s): {', '.join(shifted[:5])}")
    if profile.schema_version_changed:
        parts = []
        if profile.new_columns:
            parts.append(f"+{len(profile.new_columns)} new")
        if profile.dropped_columns:
            parts.append(f"-{len(profile.dropped_columns)} dropped")
        reasons.append(f"Schema drift ({', '.join(parts)})")
    return reasons


def _effectiveness_breach_reasons(session: Session, table_id: object) -> list[str]:
    """Alert when compaction effectiveness ratio (input/output file count) drops below 10:1.

    Move 13 surfaces wasted compute early. Workers already write ``input_files_count`` and
    ``output_files_count`` per OPTIMIZE into ``audit_log`` (see ``job_store.record_audit``
    and ``worker.py``); ``benefit_score.get_compaction_effectiveness_stats`` aggregates
    those rows. We surface that here so a sustained ratio below 10:1 -- the "poor"
    threshold -- becomes a notification instead of an insight you only see by visiting
    the dashboard.
    """
    from cryo.planner.benefit_score import get_compaction_effectiveness_stats

    try:
        # table_id is `object` here for consistency with the other breach helpers; the
        # helper expects a real uuid.UUID (see ``_quality_breach_reasons`` for the same
        # dance). Pass through whatever was handed to us; SQLAlchemy's UUID(as_uuid=True)
        # bind processor will reject a plain string and surface the ValueError here.
        stats = get_compaction_effectiveness_stats(session, table_id, lookback_days=30)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return []

    if stats.get("effectiveness") not in {"poor", "fair"}:
        return []
    ratio = stats.get("effectiveness_ratio") or 0.0
    run_count = stats.get("run_count") or 0
    if run_count == 0:
        return []
    severity = "below 10:1" if stats["effectiveness"] == "poor" else "below 20:1"
    return [
        f"Compaction effectiveness {severity}: {ratio:.1f}:1 over the last "
        f"{run_count} OPTIMIZE run(s) — raise min_small_files or cooldown to avoid "
        "wasted compute."
    ]


def _commit_race_breach_reasons(session: Session, table_id: object) -> list[str]:
    """Alert when native compaction keeps losing the optimistic-concurrency commit race.

    The Go engine tags an exhausted commit race with ``commit_race_lost`` in the failure
    detail (see ``engine-go`` ``commitRaceLostToken`` / ``runner.is_commit_race_lost``), and
    the worker persists that detail on the FAILED ``audit_log`` row. Repeated losses on a
    hot table mean compute is wasted and file count grows unchecked, yet each individual run
    just looks like a generic failure -- so we surface it as a first-class signal here rather
    than leaving it buried in a silent retry loop (docs/native-engine-scoping-research.md
    §3.2 / Scenario B).
    """
    from cryo.engines.native.runner import COMMIT_RACE_LOST_TOKEN

    settings = get_settings()
    since = datetime.now(UTC) - timedelta(hours=settings.commit_race_alert_lookback_hours)
    count = (
        session.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.table_id == table_id,
                AuditLog.ts >= since,
                AuditLog.detail.contains(COMMIT_RACE_LOST_TOKEN),
            )
        )
        or 0
    )
    if count < settings.commit_race_alert_min_occurrences:
        return []
    return [
        f"Compaction losing the commit race: {count} native OPTIMIZE run(s) in the last "
        f"{settings.commit_race_alert_lookback_hours}h lost the commit race to concurrent "
        "writes -- widen the off-peak window, reduce ingest concurrency, or enable "
        "partial-progress checkpointing so each lost attempt forfeits less work."
    ]


def check_table_breach(session: Session, table: Table) -> TableBreach:
    reasons = (
        _sla_breach_reasons(session, table)
        + _quality_breach_reasons(session, table.id)
        + _effectiveness_breach_reasons(session, table.id)
        + _commit_race_breach_reasons(session, table.id)
    )
    return TableBreach(
        table_id=str(table.id), table_name=qualified_table_name(table), reasons=reasons
    )


def _recipients_for(table: Table) -> list[str]:
    recipients = {sub.email for sub in table.alert_subscriptions}
    if table.owner_email:
        recipients.add(table.owner_email)
    return sorted(recipients)


def _recently_dispatched(session: Session, table_id: object, cooldown_hours: int) -> bool:
    since = datetime.now(UTC) - timedelta(hours=cooldown_hours)
    stmt = (
        select(TableAlertDispatch.id)
        .where(TableAlertDispatch.table_id == table_id, TableAlertDispatch.sent_at >= since)
        .limit(1)
    )
    return session.execute(stmt).first() is not None


def _build_email_body(breach: TableBreach) -> str:
    settings = get_settings()
    reasons_html = "".join(f"<li>{r}</li>" for r in breach.reasons)
    link_html = ""
    if settings.web_app_base_url:
        url = f"{settings.web_app_base_url.rstrip('/')}/tables/{breach.table_id}"
        link_html = f'<p><a href="{url}">Open {breach.table_name} in Cryo</a></p>'
    return (
        f"<p><strong>{breach.table_name}</strong> has {len(breach.reasons)} active "
        f"SLA/data-quality/effectiveness breach(es):</p><ul>{reasons_html}</ul>{link_html}"
    )


def _ai_summary_for_breach(session: Session, table_id: uuid.UUID) -> str | None:
    """Best-effort root-cause hypothesis for a Slack breach alert.

    Never raises and never blocks the alert it's attached to -- a table breach must still
    notify even when LLM generation fails, is disabled, or times out. Returns the top-ranked
    hypothesis's ``because`` text (the deterministic-evidence explanation, whether or not an
    LLM actually ranked it -- ``compose_root_cause_brief`` falls back to deterministic
    hypotheses on its own when no LLM provider is configured or evidence is empty).
    """
    if not get_settings().ai_feature_diagnosis_enabled:
        return None
    try:
        import asyncio

        from cryo.ai.anomaly_brief import compose_root_cause_brief

        brief = asyncio.run(compose_root_cause_brief(session, table_id))
        if not brief.hypotheses:
            return None
        top = brief.hypotheses[0]
        return f"{top.title} ({top.because})"
    except Exception:
        logger.warning("AI root-cause summary failed for table %s", table_id, exc_info=True)
        return None


def _dispatch(session: Session, table: Table, breach: TableBreach, recipients: list[str]) -> None:
    from cryo.integrations.alerts import send_slack_table_breach_alert
    from cryo.reporting.email import send_email

    subject = f"[Cryo] {breach.table_name} breached SLA/quality/effectiveness thresholds"
    send_email(subject, _build_email_body(breach), recipients)
    ai_summary = _ai_summary_for_breach(session, table.id)
    send_slack_table_breach_alert(breach.table_name, breach.reasons, ai_summary=ai_summary)

    session.add(
        TableAlertDispatch(table_id=table.id, reasons=breach.reasons, recipients=recipients)
    )
    session.commit()


def run_alert_dispatch_tick(session: Session) -> int:
    """Check all owned/subscribed tables for breaches; return the number of alerts sent."""
    settings = get_settings()
    stmt = (
        select(Table)
        .outerjoin(TableAlertSubscription)
        .where(Table.owner_email.isnot(None) | TableAlertSubscription.id.isnot(None))
        .options(selectinload(Table.alert_subscriptions), selectinload(Table.stats))
        .distinct()
    )
    tables = session.scalars(stmt).all()

    sent = 0
    for table in tables:
        recipients = _recipients_for(table)
        if not recipients:
            continue
        if _recently_dispatched(session, table.id, settings.table_alert_cooldown_hours):
            continue
        breach = check_table_breach(session, table)
        if not breach.reasons:
            continue
        _dispatch(session, table, breach, recipients)
        sent += 1

    return sent


def run_alert_dispatch() -> int:
    """Open a session and run one dispatch tick -- the entry point for the scheduler daemon."""
    from cryo.db.session import SessionLocal

    with SessionLocal() as session:
        return run_alert_dispatch_tick(session)
