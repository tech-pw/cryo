"""API tests for table ownership + alert subscription endpoints."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session, sessionmaker

from cryo.db.models import Source, Table
from tests.auth_helpers import ADMIN_HEADERS, OPERATOR_HEADERS, VIEWER_HEADERS
from tests.test_presets_api_db import ApiDb


def _seed_table(session_factory: sessionmaker[Session]) -> uuid.UUID:
    table_id = uuid.uuid4()
    with session_factory() as session:
        source = Source(id=uuid.uuid4(), name=f"source-{uuid.uuid4()}", type="rest", config={})
        session.add(source)
        session.flush()
        session.add(
            Table(
                id=table_id,
                source_id=source.id,
                schema_name="analytics",
                table_name="events",
                is_active=True,
            )
        )
        session.commit()
    return table_id


def test_patch_table_sets_owner_email(api_db: ApiDb) -> None:
    table_id = _seed_table(api_db.session_factory)
    client = api_db.client

    response = client.patch(
        f"/api/v1/tables/{table_id}",
        headers=OPERATOR_HEADERS,
        json={"owner_email": "owner@example.com"},
    )

    assert response.status_code == 200
    assert response.json()["owner_email"] == "owner@example.com"

    fetched = client.get(f"/api/v1/tables/{table_id}", headers=VIEWER_HEADERS)
    assert fetched.json()["owner_email"] == "owner@example.com"


def test_patch_table_rejects_viewer_role(api_db: ApiDb) -> None:
    table_id = _seed_table(api_db.session_factory)

    response = api_db.client.patch(
        f"/api/v1/tables/{table_id}",
        headers=VIEWER_HEADERS,
        json={"owner_email": "owner@example.com"},
    )

    assert response.status_code == 403


def test_deactivate_tables_batch_deactivates_exactly_the_requested_ids(api_db: ApiDb) -> None:
    """User-requested, 2026-09-09: an explicit, admin-reviewed batch deactivate for
    '*_evolving' tables that don't fit the automated sweep's location IS NULL safety
    condition. Must touch exactly the requested ids -- no filter, no heuristic expansion."""
    table_id_1 = _seed_table(api_db.session_factory)
    table_id_2 = _seed_table(api_db.session_factory)
    untouched_id = _seed_table(api_db.session_factory)
    client = api_db.client

    response = client.post(
        "/api/v1/tables/deactivate-batch",
        headers=ADMIN_HEADERS,
        json={"table_ids": [str(table_id_1), str(table_id_2)]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["requested"] == 2
    assert body["deactivated"] == 2
    assert set(body["deactivated_ids"]) == {str(table_id_1), str(table_id_2)}

    with api_db.session_factory() as session:
        from cryo.db.models import Table

        assert session.get(Table, table_id_1).is_active is False
        assert session.get(Table, table_id_2).is_active is False
        assert session.get(Table, untouched_id).is_active is True


def test_deactivate_tables_batch_rejects_operator_role(api_db: ApiDb) -> None:
    """Requires admin, not just operator -- stricter than cancel_run, since this is a
    caller-trusted (not automatically-verified) deactivation."""
    table_id = _seed_table(api_db.session_factory)

    response = api_db.client.post(
        "/api/v1/tables/deactivate-batch",
        headers=OPERATOR_HEADERS,
        json={"table_ids": [str(table_id)]},
    )

    assert response.status_code == 403


def test_deactivate_tables_batch_404s_on_unknown_id_and_applies_nothing(api_db: ApiDb) -> None:
    """A bad id must fail the whole batch, not silently skip it -- get_scoped_resource raises
    404 for any unmatched id before deactivate_tables_by_id ever runs, so a typo can't
    partially apply."""
    table_id = _seed_table(api_db.session_factory)
    bogus_id = uuid.uuid4()

    response = api_db.client.post(
        "/api/v1/tables/deactivate-batch",
        headers=ADMIN_HEADERS,
        json={"table_ids": [str(table_id), str(bogus_id)]},
    )

    assert response.status_code == 404
    with api_db.session_factory() as session:
        from cryo.db.models import Table

        assert session.get(Table, table_id).is_active is True


def test_subscribe_list_and_unsubscribe_round_trip(api_db: ApiDb) -> None:
    table_id = _seed_table(api_db.session_factory)
    client = api_db.client

    create = client.post(
        f"/api/v1/tables/{table_id}/alert-subscriptions",
        headers=VIEWER_HEADERS,
        json={"email": "consumer@example.com"},
    )
    assert create.status_code == 201
    subscription_id = create.json()["id"]
    assert create.json()["email"] == "consumer@example.com"

    listing = client.get(f"/api/v1/tables/{table_id}/alert-subscriptions", headers=VIEWER_HEADERS)
    assert listing.status_code == 200
    assert [s["email"] for s in listing.json()] == ["consumer@example.com"]

    delete = client.delete(
        f"/api/v1/tables/{table_id}/alert-subscriptions/{subscription_id}",
        headers=VIEWER_HEADERS,
    )
    assert delete.status_code == 204

    listing_after = client.get(
        f"/api/v1/tables/{table_id}/alert-subscriptions", headers=VIEWER_HEADERS
    )
    assert listing_after.json() == []


def test_resubscribing_same_email_is_idempotent(api_db: ApiDb) -> None:
    table_id = _seed_table(api_db.session_factory)
    client = api_db.client

    first = client.post(
        f"/api/v1/tables/{table_id}/alert-subscriptions",
        headers=VIEWER_HEADERS,
        json={"email": "consumer@example.com"},
    )
    second = client.post(
        f"/api/v1/tables/{table_id}/alert-subscriptions",
        headers=VIEWER_HEADERS,
        json={"email": "consumer@example.com"},
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]

    listing = client.get(f"/api/v1/tables/{table_id}/alert-subscriptions", headers=VIEWER_HEADERS)
    assert len(listing.json()) == 1


def test_delete_alert_subscription_404s_for_wrong_table(api_db: ApiDb) -> None:
    table_id = _seed_table(api_db.session_factory)
    other_table_id = _seed_table(api_db.session_factory)
    client = api_db.client

    create = client.post(
        f"/api/v1/tables/{table_id}/alert-subscriptions",
        headers=VIEWER_HEADERS,
        json={"email": "consumer@example.com"},
    )
    subscription_id = create.json()["id"]

    response = client.delete(
        f"/api/v1/tables/{other_table_id}/alert-subscriptions/{subscription_id}",
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 404
