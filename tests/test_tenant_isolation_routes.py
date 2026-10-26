"""Cross-tenant access to by-ID and list routes (#187).

With ``CRYO_ENFORCE_TENANT_ISOLATION`` on and tenant-scoped tokens, a principal in tenant A
must not be able to read or change tenant B's runs, jobs, presets, exclusions, quality
constraints, layout recommendations or table properties. Each by-ID route answers 404 for
another tenant's ID (indistinguishable from an unknown one) and list routes only return the
caller's own rows. The same calls with tenant B's own token still work, so the 404s come from
scoping rather than from broken seed data.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy.orm import Session

from cryo.api.deps import CurrentPrincipal, get_tenant_session
from cryo.config import get_settings
from cryo.db.models import (
    Destination,
    LayoutRecommendation,
    MaintenanceExclusion,
    MaintenanceJob,
    MaintenanceRun,
    Preset,
    PresetRule,
    QualityConstraint,
    Source,
    Table,
    TableAlertSubscription,
    Tenant,
)
from cryo.db.tenant_scope import bind_tenant
from cryo.domain.enums import EngineType, JobStatus, Mode
from tests.auth_helpers import auth_header


@dataclass(frozen=True)
class TenantRows:
    tenant_id: uuid.UUID
    source_id: uuid.UUID
    table_id: uuid.UUID
    preset_id: uuid.UUID
    rule_id: uuid.UUID
    run_id: uuid.UUID
    scan_run_id: uuid.UUID
    job_id: uuid.UUID
    recommendation_id: uuid.UUID
    constraint_id: uuid.UUID
    exclusion_id: uuid.UUID
    subscription_id: uuid.UUID


@pytest.fixture()
def tenant_api(api_db, monkeypatch: pytest.MonkeyPatch):
    settings = get_settings().model_copy(update={"enforce_tenant_isolation": True})
    monkeypatch.setattr("cryo.db.tenant_scope.get_settings", lambda: settings)

    def bound_session(principal: CurrentPrincipal) -> Iterator[Session]:
        with api_db.session_factory() as session:
            bind_tenant(session, principal.tenant_id)
            yield session

    app: Any = api_db.client.app
    app.dependency_overrides[get_tenant_session] = bound_session
    return api_db


def _seed_tenant(api_db, label: str) -> TenantRows:
    tenant_id = uuid.uuid4()
    source_id, destination_id, table_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    preset_id, rule_id = uuid.uuid4(), uuid.uuid4()
    run_id, scan_run_id, job_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    recommendation_id, constraint_id = uuid.uuid4(), uuid.uuid4()
    exclusion_id, subscription_id = uuid.uuid4(), uuid.uuid4()
    with api_db.session_factory() as session:
        session.add(
            Tenant(
                id=tenant_id,
                name=f"{label} {tenant_id.hex[:8]}",
                slug=f"{label}-{tenant_id.hex[:8]}",
                admin_user_id=f"{label}-admin",
            )
        )
        session.flush()
        session.add_all(
            [
                Source(
                    id=source_id,
                    tenant_id=tenant_id,
                    name=f"{label}-source-{source_id.hex[:8]}",
                    type="rest",
                    config={},
                ),
                Destination(
                    id=destination_id,
                    tenant_id=tenant_id,
                    name=f"{label}-dest-{destination_id.hex[:8]}",
                    engine=EngineType.TRINO.value,
                    config={},
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                Table(
                    id=table_id,
                    tenant_id=tenant_id,
                    source_id=source_id,
                    schema_name="analytics",
                    table_name=f"events_{label}",
                    is_active=True,
                ),
                Preset(
                    id=preset_id,
                    tenant_id=tenant_id,
                    name=f"{label}-preset-{preset_id.hex[:8]}",
                    destination_id=destination_id,
                    operations=["optimize"],
                    params={},
                    priority=100,
                    guardrails={},
                    enabled=True,
                    is_default=False,
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                PresetRule(id=rule_id, preset_id=preset_id, predicate={"all": []}, enabled=True),
                MaintenanceRun(
                    id=run_id,
                    kind="adhoc",
                    trigger="test",
                    status=JobStatus.QUEUED.value,
                    preset_id=preset_id,
                ),
                MaintenanceRun(
                    id=scan_run_id,
                    kind="scan",
                    trigger="test",
                    status="succeeded",
                    summary={"source_id": str(source_id)},
                ),
                LayoutRecommendation(
                    id=recommendation_id,
                    table_id=table_id,
                    recommended_strategy="sort",
                    recommended_columns=["event_date"],
                    analysis_query_count=0,
                    confidence=0.9,
                    status="pending",
                ),
                QualityConstraint(
                    id=constraint_id,
                    table_id=table_id,
                    name=f"{label}-rowcount",
                    kind="size",
                    params={"min": 1},
                    severity="error",
                    enabled=True,
                ),
                MaintenanceExclusion(
                    id=exclusion_id,
                    source_id=source_id,
                    schema_name="analytics",
                    table_name=None,
                    reason="tenant isolation test",
                    created_by=f"{label}-operator",
                ),
                TableAlertSubscription(
                    id=subscription_id,
                    table_id=table_id,
                    email=f"{label}@example.com",
                    created_by=f"{label}-viewer",
                ),
            ]
        )
        session.flush()
        session.add(
            MaintenanceJob(
                id=job_id,
                run_id=run_id,
                table_id=table_id,
                destination_id=destination_id,
                mode=Mode.STANDARD.value,
                status=JobStatus.QUEUED.value,
            )
        )
        session.commit()
    return TenantRows(
        tenant_id=tenant_id,
        source_id=source_id,
        table_id=table_id,
        preset_id=preset_id,
        rule_id=rule_id,
        run_id=run_id,
        scan_run_id=scan_run_id,
        job_id=job_id,
        recommendation_id=recommendation_id,
        constraint_id=constraint_id,
        exclusion_id=exclusion_id,
        subscription_id=subscription_id,
    )


@pytest.fixture()
def tenants(tenant_api) -> tuple[TenantRows, TenantRows]:
    return _seed_tenant(tenant_api, "a"), _seed_tenant(tenant_api, "b")


def _headers(role: str, rows: TenantRows) -> dict[str, str]:
    return auth_header(role, tenant_id=rows.tenant_id)


def _cross_tenant_calls(b: TenantRows) -> list[tuple[str, str, dict[str, Any] | None]]:
    """Every by-ID route from #187, aimed at tenant B's rows."""
    return [
        # runs and jobs
        ("GET", f"/api/v1/runs/{b.run_id}", None),
        ("GET", f"/api/v1/runs/{b.scan_run_id}", None),
        ("GET", f"/api/v1/runs/{b.run_id}/jobs", None),
        ("GET", f"/api/v1/runs/{b.run_id}/skip-evaluations", None),
        ("POST", f"/api/v1/runs/{b.run_id}/cancel", None),
        ("GET", f"/api/v1/jobs/{b.job_id}", None),
        ("POST", f"/api/v1/jobs/{b.job_id}/force-admit", None),
        # presets and rules
        ("DELETE", f"/api/v1/presets/{b.preset_id}", None),
        ("GET", f"/api/v1/presets/{b.preset_id}/rules", None),
        ("POST", f"/api/v1/presets/{b.preset_id}/rules", {"predicate": {"all": []}}),
        ("DELETE", f"/api/v1/presets/{b.preset_id}/rules/{b.rule_id}", None),
        ("POST", f"/api/v1/presets/{b.preset_id}/simulate", None),
        # table properties
        (
            "POST",
            f"/api/v1/tables/{b.table_id}/properties/apply",
            {"properties": {"write.target-file-size-bytes": "134217728"}},
        ),
        ("GET", f"/api/v1/tables/{b.table_id}/property-recommendations", None),
        # layout recommendations
        (
            "POST",
            f"/api/v1/smart-maintenance/layout-recommendations/{b.recommendation_id}/approve",
            {},
        ),
        (
            "POST",
            f"/api/v1/smart-maintenance/layout-recommendations/{b.recommendation_id}/dismiss",
            {"reason": "not mine"},
        ),
        (
            "POST",
            f"/api/v1/smart-maintenance/layout-recommendations/{b.recommendation_id}/execute",
            {},
        ),
        ("GET", f"/api/v1/smart-maintenance/tables/{b.table_id}/roi", None),
        ("GET", f"/api/v1/smart-maintenance/tables/{b.table_id}/workload-forecast", None),
        # quality constraints
        ("GET", f"/api/v1/tables/{b.table_id}/quality-constraints", None),
        (
            "POST",
            f"/api/v1/tables/{b.table_id}/quality-constraints",
            {"name": "x", "kind": "size", "params": {"min": 1}},
        ),
        ("POST", f"/api/v1/tables/{b.table_id}/quality-constraints/evaluate", None),
        ("PATCH", f"/api/v1/quality-constraints/{b.constraint_id}", {"enabled": False}),
        ("DELETE", f"/api/v1/quality-constraints/{b.constraint_id}", None),
        ("GET", f"/api/v1/quality-constraints/{b.constraint_id}/results", None),
        # exclusions
        (
            "POST",
            "/api/v1/maintenance/exclusions",
            {"source_id": str(b.source_id), "schema_name": "other", "reason": "not mine"},
        ),
        ("DELETE", f"/api/v1/maintenance/exclusions/{b.exclusion_id}", None),
    ]


def test_cross_tenant_by_id_routes_answer_404(tenant_api, tenants) -> None:
    a, b = tenants
    failures: list[str] = []
    for method, path, body in _cross_tenant_calls(b):
        role = "admin"  # highest role: only tenancy, never RBAC, may refuse these
        response = tenant_api.client.request(method, path, headers=_headers(role, a), json=body)
        if response.status_code != 404:
            failures.append(f"{method} {path} -> {response.status_code} {response.text[:200]}")
    assert not failures, "cross-tenant calls not refused with 404:\n" + "\n".join(failures)


def test_cross_tenant_writes_leave_other_tenant_rows_untouched(tenant_api, tenants) -> None:
    a, b = tenants
    for method, path, body in _cross_tenant_calls(b):
        tenant_api.client.request(method, path, headers=_headers("admin", a), json=body)
    with tenant_api.session_factory() as session:
        run = session.get(MaintenanceRun, b.run_id)
        job = session.get(MaintenanceJob, b.job_id)
        preset = session.get(Preset, b.preset_id)
        recommendation = session.get(LayoutRecommendation, b.recommendation_id)
        assert run is not None and run.status == JobStatus.QUEUED.value
        assert job is not None and job.status == JobStatus.QUEUED.value
        assert not job.force_admit
        assert preset is not None and preset.enabled
        assert session.get(PresetRule, b.rule_id) is not None
        assert recommendation is not None and recommendation.status == "pending"
        assert session.get(QualityConstraint, b.constraint_id) is not None
        assert session.get(MaintenanceExclusion, b.exclusion_id) is not None


def test_same_tenant_by_id_reads_still_work(tenant_api, tenants) -> None:
    _, b = tenants
    headers = _headers("admin", b)
    for path in (
        f"/api/v1/runs/{b.run_id}",
        f"/api/v1/runs/{b.scan_run_id}",
        f"/api/v1/runs/{b.run_id}/jobs",
        f"/api/v1/jobs/{b.job_id}",
        f"/api/v1/presets/{b.preset_id}/rules",
        f"/api/v1/tables/{b.table_id}/quality-constraints",
        f"/api/v1/quality-constraints/{b.constraint_id}/results",
    ):
        response = tenant_api.client.get(path, headers=headers)
        assert response.status_code == 200, f"{path} -> {response.status_code} {response.text}"


def test_list_routes_only_return_callers_tenant(tenant_api, tenants) -> None:
    a, b = tenants
    headers = _headers("viewer", a)

    runs = tenant_api.client.get("/api/v1/runs", headers=headers)
    assert runs.status_code == 200
    run_ids = {row["id"] for row in runs.json()}
    assert run_ids == {str(a.run_id), str(a.scan_run_id)}

    exclusions = tenant_api.client.get("/api/v1/maintenance/exclusions", headers=headers)
    assert exclusions.status_code == 200
    assert [row["id"] for row in exclusions.json()] == [str(a.exclusion_id)]

    compatible = tenant_api.client.get(
        f"/api/v1/tables/{a.table_id}/compatible-presets", headers=headers
    )
    assert compatible.status_code == 200
    assert str(b.preset_id) not in {row["id"] for row in compatible.json()}

    recommendations = tenant_api.client.get(
        "/api/v1/smart-maintenance/layout-recommendations", headers=headers
    )
    assert recommendations.status_code == 200
    assert [row["id"] for row in recommendations.json()["items"]] == [str(a.recommendation_id)]


def test_assignments_refuse_another_tenants_preset_and_tables(tenant_api, tenants) -> None:
    a, b = tenants
    headers = _headers("operator", a)

    foreign_preset = tenant_api.client.post(
        "/api/v1/assignments",
        headers=headers,
        json={
            "selection": {"mode": "ids", "ids": [str(a.table_id)]},
            "preset_id": str(b.preset_id),
        },
    )
    assert foreign_preset.status_code == 400
    assert foreign_preset.json()["detail"] == "Preset does not exist"

    foreign_tables = tenant_api.client.post(
        "/api/v1/assignments",
        headers=headers,
        json={
            "selection": {"mode": "ids", "ids": [str(b.table_id)]},
            "preset_id": str(a.preset_id),
        },
    )
    assert foreign_tables.status_code == 400
    assert str(b.table_id) in foreign_tables.json()["detail"]


def test_alert_subscription_delete_requires_subscriber_or_admin(api_db) -> None:
    with api_db.session_factory() as session:
        source = Source(id=uuid.uuid4(), name=f"subs-{uuid.uuid4()}", type="rest", config={})
        session.add(source)
        session.flush()
        table = Table(
            id=uuid.uuid4(),
            source_id=source.id,
            schema_name="analytics",
            table_name="subs",
            is_active=True,
        )
        session.add(table)
        session.commit()
        table_id = table.id

    def subscribe(email: str) -> str:
        response = api_db.client.post(
            f"/api/v1/tables/{table_id}/alert-subscriptions",
            headers=auth_header("viewer"),  # subject "test-user"
            json={"email": email},
        )
        assert response.status_code == 201, response.text
        return str(response.json()["id"])

    own = subscribe("watcher@example.com")
    with api_db.session_factory() as session:
        row = session.get(TableAlertSubscription, uuid.UUID(own))
        assert row is not None and row.created_by == "test-user"
        # Someone else's subscription (and a legacy row with no recorded creator).
        session.add_all(
            [
                TableAlertSubscription(
                    id=uuid.uuid4(),
                    table_id=table_id,
                    email="other@example.com",
                    created_by="someone-else",
                ),
                TableAlertSubscription(
                    id=uuid.uuid4(), table_id=table_id, email="legacy@example.com"
                ),
            ]
        )
        session.commit()
        others = {
            sub.email: str(sub.id)
            for sub in session.query(TableAlertSubscription).filter(
                TableAlertSubscription.table_id == table_id
            )
        }

    base = f"/api/v1/tables/{table_id}/alert-subscriptions"
    viewer = auth_header("viewer")
    for email in ("other@example.com", "legacy@example.com"):
        refused = api_db.client.delete(f"{base}/{others[email]}", headers=viewer)
        assert refused.status_code == 403, email

    assert api_db.client.delete(f"{base}/{own}", headers=viewer).status_code == 204
    admin = auth_header("admin")
    assert (
        api_db.client.delete(f"{base}/{others['other@example.com']}", headers=admin).status_code
        == 204
    )
