"""Shared maintenance-run entry. The API route and the action gateway both call this."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cryo.api.schemas import MaintenanceRunRequest, MaintenanceRunResponse, SkippedTableOps
from cryo.api.security import Principal
from cryo.db.repositories.exclusions import excluded_table_ids
from cryo.db.repositories.job_dedupe import active_coverage
from cryo.db.repositories.presets import SelectionResult
from cryo.domain.enums import JobStatus
from cryo.intents.binding import build_and_bind_job_intent
from cryo.observability.correlation import correlation_summary
from cryo.planner.guardrails import sort_operations_canonically
from cryo.planner.job_coverage import (
    Coverage,
    canonical_operations,
    dedupe_key,
    mode_operations,
    uncovered,
)
from cryo.queue.run_events import build_job_status, build_log_line, safe_publish

_DEDUPE_DETAIL_LIMIT = 200


def _dedupe_summary(skipped: list[SkippedTableOps]) -> dict[str, object]:
    return {
        "tables_skipped": sum(1 for entry in skipped if entry.fully_skipped),
        "steps_skipped": sum(len(entry.operations) for entry in skipped),
        "details": [
            {
                "table_id": str(entry.table_id),
                "operations": entry.operations,
                "covering_job_ids": entry.covering_job_ids,
                "fully_skipped": entry.fully_skipped,
            }
            for entry in skipped[:_DEDUPE_DETAIL_LIMIT]
        ],
    }


def _already_covered_detail(skipped: list[SkippedTableOps]) -> dict[str, object]:
    return {
        "code": "already_queued",
        "message": (
            "Every selected table already has a queued or running job covering these "
            "operations. Re-submit with force to run them anyway."
        ),
        "skipped": [
            {
                "table_id": str(entry.table_id),
                "operations": entry.operations,
                "covering_job_ids": entry.covering_job_ids,
            }
            for entry in skipped[:_DEDUPE_DETAIL_LIMIT]
        ],
    }


def start_maintenance_run(
    session: Session,
    payload: MaintenanceRunRequest,
    principal: Principal,
    *,
    allow_operations_outside_preset: bool = False,
) -> MaintenanceRunResponse:
    # Look up helpers on the routes module so existing tests that patch
    # routes.MaintenanceRun / _resolve_selection / build_runtime_wiring still apply.
    from cryo.api import routes as api_routes

    destination = session.get(api_routes.Destination, payload.destination_id)
    if destination is None:
        raise HTTPException(status_code=400, detail="Destination does not exist")
    if str(getattr(destination, "engine", "") or "") == "flink":
        raise HTTPException(
            status_code=422,
            detail="Flink is config-only. Cryo does not run maintenance on this destination.",
        )
    preset = session.get(api_routes.Preset, payload.preset_id)
    if preset is None:
        raise HTTPException(status_code=400, detail="Preset does not exist")
    if getattr(preset, "paused", False):
        if not payload.override_pause:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Preset {preset.name} is paused. Resume it, or pass override_pause=true "
                    "to run it once anyway (audited)."
                ),
            )
        session.add(
            api_routes.AuditLog(
                action="preset_pause_overridden",
                status="OK",
                engine="cryo",
                detail=f"preset={preset.name} preset_id={preset.id} manual run while paused",
                actor=principal.subject,
            )
        )

    requested_ops: list[str] = []
    if preset is not None:
        for op in preset.operations or []:
            if isinstance(op, str):
                requested_ops.append(op)
            elif isinstance(op, dict) and "type" in op:
                requested_ops.append(str(op["type"]))
    # Presets are stored canonically (sorted on write, plus a backfill migration), but a
    # hand-edited row must not brick the run: build_plan sorts by CORRECT_ORDER regardless,
    # so the executed order is canonical either way. Normalizing here keeps what we hand the
    # planner consistent with what actually runs, rather than rejecting the request.
    requested_ops = sort_operations_canonically(requested_ops)
    if payload.operations is not None:
        preset_ops = set(requested_ops)
        if not allow_operations_outside_preset and not set(payload.operations) <= preset_ops:
            raise HTTPException(
                status_code=400,
                detail="operations must be a subset of the preset's operations",
            )
        requested_ops = sort_operations_canonically(list(payload.operations))

    if payload.scheduled_at is not None:
        now = datetime.now(UTC)
        scheduled_at = payload.scheduled_at
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=UTC)
        if scheduled_at <= now:
            raise HTTPException(status_code=400, detail="scheduled_at must be in the future")

    selection = (
        api_routes._resolve_selection(session, payload.selection)
        if payload.selection is not None
        else api_routes._resolve_selection(
            session,
            api_routes.AssignmentSelection(mode="ids", ids=payload.table_ids, filter=None),
        )
    )
    excluded = excluded_table_ids(session, selection.table_ids)
    if excluded:
        kept = [table_id for table_id in selection.table_ids if table_id not in excluded]
        if not kept:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Every selected table is excluded from maintenance. "
                    "Include it on the Runs page first."
                ),
            )
        selection = SelectionResult(kept)

    # A scheduled-for-later run is exempt: its jobs sit at status="queued" until dispatch, so
    # deduping now would compare against work that may be finished by then.
    dedupe_enabled = not payload.force and payload.scheduled_at is None
    wanted_ops: tuple[str, ...] = canonical_operations(
        requested_ops if payload.operations is not None else mode_operations(payload.mode)
    )
    coverage_by_table: dict[str, Coverage] = {}
    if dedupe_enabled and wanted_ops:
        active = active_coverage(session, selection.table_ids)
        for table_id in selection.table_ids:
            coverage_by_table[str(table_id)] = uncovered(wanted_ops, active.get(str(table_id), []))
    skipped: list[SkippedTableOps] = []
    table_ids_to_run: list[uuid.UUID] = []
    for table_id in selection.table_ids:
        coverage = coverage_by_table.get(str(table_id))
        if coverage is not None and coverage.skipped:
            skipped.append(
                SkippedTableOps(
                    table_id=table_id,
                    operations=list(coverage.skipped),
                    covering_job_ids=list(coverage.covering_job_ids),
                    fully_skipped=coverage.fully_covered,
                )
            )
        if coverage is None or not coverage.fully_covered:
            table_ids_to_run.append(table_id)
    if not table_ids_to_run:
        raise HTTPException(status_code=409, detail=_already_covered_detail(skipped))

    summary = correlation_summary({"actor": principal.subject})
    if excluded:
        summary["excluded_tables"] = len(excluded)
    if payload.operations is not None:
        summary["operations"] = list(requested_ops)
    if skipped:
        summary["dedupe"] = _dedupe_summary(skipped)

    run = api_routes.MaintenanceRun(
        kind="adhoc",
        preset_id=payload.preset_id,
        trigger=payload.trigger,
        status="scheduled" if payload.scheduled_at is not None else "queued",
        scheduled_at=payload.scheduled_at,
        summary=summary,
    )
    session.add(run)
    session.flush()

    # Plan Verifier (arXiv:2510.09567) -- when the preset exposes a parsed MaintenancePlan via
    # params.operations (Planner-emitted), run the verifier once per table before enqueue and
    # persist the verdict onto the job. If the verifier reports a critical violation, refuse
    # to enqueue.
    settings = api_routes.get_settings()
    verifier_enabled = settings.plan_verifier_enabled
    # Computed once for the whole run, not per-table -- avoids an N+1 query pattern and matches
    # how the scheduler's cron path (_apply_routing) resolves routing for a run as a batch.
    adaptive_destinations = api_routes._adaptive_routing_destinations_for_run(
        session, preset, table_ids_to_run
    )
    jobs: list[api_routes.MaintenanceJob] = []
    for table_id in table_ids_to_run:
        if preset is not None and requested_ops:
            for op_name in requested_ops:
                from cryo.domain.enums import OperationType
                from cryo.security.abac_enforcement import evaluate_maintenance_allowed

                try:
                    op_enum = OperationType(op_name)
                except ValueError:
                    op_enum = op_name  # type: ignore[assignment]
                allowed, reason = evaluate_maintenance_allowed(
                    session, table_id, principal, op_enum
                )
                if not allowed:
                    raise HTTPException(status_code=403, detail=reason)
        plan_for_verifier = (
            None
            if settings.durable_intents_enabled
            else api_routes._build_plan_for_table(session, table_id, preset, payload.mode)
        )
        verified_flag = True
        plan_hash_value: str | None = None
        verifier_version_value: str | None = None
        violations_value: list[dict[str, object]] = []
        if verifier_enabled and plan_for_verifier is not None:
            from cryo.planner.verifier import PlanVerifier

            verdict = PlanVerifier(session=session, settings=api_routes.get_settings()).verify(
                plan_for_verifier
            )
            verified_flag = verdict.verified
            plan_hash_value = verdict.plan_hash
            verifier_version_value = verdict.verifier_version
            violations_value = [
                {
                    "invariant": v.invariant,
                    "severity": v.severity,
                    "message": v.message,
                    "location": v.location,
                }
                for v in verdict.violations
            ]
            if not verdict.verified:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "plan_verifier_rejected",
                        "plan_hash": verdict.plan_hash,
                        "violations": violations_value,
                    },
                )
        routed_destination_id = adaptive_destinations.get(str(table_id))
        coverage = coverage_by_table.get(str(table_id))
        trimmed_operations = (
            list(coverage.remaining)
            if coverage is not None and coverage.partially_covered
            else None
        )
        job = api_routes.MaintenanceJob(
            run_id=run.id,
            table_id=table_id,
            destination_id=(
                uuid.UUID(routed_destination_id)
                if routed_destination_id is not None
                else payload.destination_id
            ),
            mode=payload.mode.value,
            operations=trimmed_operations,
            dedupe_key=dedupe_key(trimmed_operations or wanted_ops) if dedupe_enabled else None,
            status="queued",
            verified=verified_flag,
            plan_hash=plan_hash_value,
            verifier_version=verifier_version_value,
            verifier_violations=violations_value or None,
        )
        try:
            # A concurrent run can take the (table, operations) slot between the coverage read
            # and this insert; the unique index rejects us and the savepoint keeps the run alive.
            with session.begin_nested():
                session.add(job)
        except IntegrityError:
            skipped.append(
                SkippedTableOps(
                    table_id=table_id,
                    operations=list(trimmed_operations or wanted_ops),
                    covering_job_ids=[],
                    fully_skipped=True,
                )
            )
            continue
        if settings.durable_intents_enabled:
            build_and_bind_job_intent(
                session,
                job_id=str(job.id),
                settings=settings,
                actor_type="api",
                actor_id=principal.subject,
            )
        jobs.append(job)
    if not jobs:
        session.rollback()
        raise HTTPException(status_code=409, detail=_already_covered_detail(skipped))
    if skipped:
        run.summary = {**(run.summary or {}), "dedupe": _dedupe_summary(skipped)}
    session.commit()
    wiring = api_routes.build_runtime_wiring()
    if payload.scheduled_at is not None:
        # Scheduled runs are not enqueued now -- the jobs exist (status="queued" at the row
        # level, same as any other job) but wiring.queue.enqueue() is what actually makes a
        # job eligible for Governor admission, and that call is deliberately skipped here.
        # cryo/scheduler/service.py's dispatch tick enqueues these once scheduled_at passes.
        safe_publish(
            wiring.run_events,
            run.id,
            build_log_line(
                run_id=run.id,
                message=(
                    f"Scheduled maintenance run with {len(jobs)} job(s) for "
                    f"{payload.scheduled_at.isoformat()}"
                ),
            ),
        )
        return MaintenanceRunResponse(
            run_id=run.id, job_ids=[job.id for job in jobs], status=run.status, skipped=skipped
        )
    safe_publish(
        wiring.run_events,
        run.id,
        build_log_line(
            run_id=run.id,
            message=f"Submitted maintenance run with {len(jobs)} job(s)",
        ),
    )
    for job in jobs:
        session.refresh(job)
        wiring.queue.enqueue(str(job.id), str(job.destination_id))
        safe_publish(
            wiring.run_events,
            run.id,
            build_job_status(
                run_id=run.id,
                job_id=job.id,
                status=JobStatus.QUEUED.value,
                table_id=job.table_id,
                message="Job queued",
            ),
        )
        safe_publish(
            wiring.run_events,
            run.id,
            build_log_line(
                run_id=run.id,
                job_id=job.id,
                table_id=job.table_id,
                message=f"Queued maintenance job for table {job.table_id}",
            ),
        )
    return MaintenanceRunResponse(
        run_id=run.id, job_ids=[job.id for job in jobs], status=run.status, skipped=skipped
    )
