from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select

from cryo.db.models import AuditLog, Destination, MaintenanceControlState, Tenant
from tests.auth_helpers import (
    ADMIN_HEADERS,
    OPERATOR_HEADERS,
    auth_header,
)


def test_global_pause_and_resume_are_durable_and_audited(api_db) -> None:
    pause = api_db.client.post(
        "/api/v1/maintenance/control/pause",
        headers=ADMIN_HEADERS,
        json={
            "scope_type": "global",
            "reason": "incident containment",
        },
    )

    assert pause.status_code == 200
    assert pause.json()["paused"] is True
    assert pause.json()["generation"] == 1

    listed = api_db.client.get(
        "/api/v1/maintenance/control",
        headers=OPERATOR_HEADERS,
    )
    assert listed.status_code == 200
    assert listed.json()[0]["scope_id"] == "global"

    resume = api_db.client.post(
        "/api/v1/maintenance/control/resume",
        headers=ADMIN_HEADERS,
        json={
            "scope_type": "global",
            "reason": "incident resolved",
        },
    )
    assert resume.status_code == 200
    assert resume.json()["paused"] is False
    assert resume.json()["generation"] == 2

    with api_db.session_factory() as session:
        state = session.scalar(select(MaintenanceControlState))
        assert state is not None
        assert state.paused is False
        actions = list(
            session.scalars(
                select(AuditLog.action)
                .where(AuditLog.action.in_(["maintenance_pause", "maintenance_resume"]))
                .order_by(AuditLog.id)
            )
        )
    assert actions == ["maintenance_pause", "maintenance_resume"]


def test_operator_cannot_apply_global_pause(api_db) -> None:
    response = api_db.client.post(
        "/api/v1/maintenance/control/pause",
        headers=OPERATOR_HEADERS,
        json={
            "scope_type": "global",
            "reason": "not authorized globally",
        },
    )

    assert response.status_code == 403


def test_tenant_operator_only_sees_and_controls_own_destination(api_db) -> None:
    tenant_a = Tenant(
        id=uuid4(),
        name="Tenant A",
        slug=f"tenant-a-{uuid4()}",
        admin_user_id="tenant-a-admin",
    )
    tenant_b = Tenant(
        id=uuid4(),
        name="Tenant B",
        slug=f"tenant-b-{uuid4()}",
        admin_user_id="tenant-b-admin",
    )
    destination_a = Destination(
        id=uuid4(),
        tenant_id=tenant_a.id,
        name=f"destination-a-{uuid4()}",
        engine="trino",
        config={},
    )
    destination_b = Destination(
        id=uuid4(),
        tenant_id=tenant_b.id,
        name=f"destination-b-{uuid4()}",
        engine="trino",
        config={},
    )
    with api_db.session_factory() as session:
        session.add_all([tenant_a, tenant_b, destination_a, destination_b])
        session.commit()

    for destination in (destination_a, destination_b):
        response = api_db.client.post(
            "/api/v1/maintenance/control/pause",
            headers=ADMIN_HEADERS,
            json={
                "scope_type": "destination",
                "scope_id": str(destination.id),
                "reason": "contain destination incident",
            },
        )
        assert response.status_code == 200

    tenant_headers = auth_header("operator", tenant_id=tenant_a.id)
    listed = api_db.client.get(
        "/api/v1/maintenance/control",
        headers=tenant_headers,
    )

    assert listed.status_code == 200
    assert {item["scope_id"] for item in listed.json()} == {str(destination_a.id)}

    forbidden = api_db.client.post(
        "/api/v1/maintenance/control/resume",
        headers=tenant_headers,
        json={
            "scope_type": "destination",
            "scope_id": str(destination_b.id),
            "reason": "attempt cross-tenant resume",
        },
    )
    assert forbidden.status_code == 403
