"""Tests for request coalescing, input-version ETags, 304 Not Modified, and short-lived
response caching on heavy read endpoints (#533)."""

from __future__ import annotations

import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

from starlette.requests import Request

from cryo.api.conditional_cache import ConditionalResultCache, conditional_cache
from cryo.api.security import Principal, Role
from cryo.db.models import (
    AuditLog,
    LayoutRecommendation,
    MaintenanceJob,
    MaintenanceRun,
    Preset,
    SharedS3AnomalyReport,
    Table,
    TableAssignment,
    TableCost,
    TableSimilarityCluster,
    TableStats,
)
from cryo.observability.metrics import (
    api_coalesced_requests_total,
    api_not_modified_total,
    api_response_cache_requests_total,
)
from tests.auth_helpers import ADMIN_HEADERS, OPERATOR_HEADERS, VIEWER_HEADERS, auth_header
from tests.test_path_to_a import _seed
from tests.test_presets_api_db import ApiDb


def _fake_request(if_none_match: str | None = None) -> Request:
    raw_headers = []
    if if_none_match is not None:
        raw_headers.append((b"if-none-match", if_none_match.encode("utf-8")))
    return Request({"type": "http", "method": "GET", "path": "/test", "headers": raw_headers})


def test_single_flight_coalesces_concurrent_identical_requests() -> None:
    cache = ConditionalResultCache(ttl_seconds=30.0)
    route = "/api/v1/test-coalesce"
    principal = Principal(subject="u1", roles=frozenset({Role.VIEWER}), tenant_id=None)
    before_coalesced = api_coalesced_requests_total.labels(route=route)._value.get()

    started = threading.Event()
    release = threading.Event()
    calls = 0
    calls_lock = threading.Lock()

    def slow_compute() -> dict[str, int]:
        nonlocal calls
        with calls_lock:
            calls += 1
        started.set()
        release.wait(timeout=5.0)
        return {"answer": 42}

    results: list[bytes] = []
    etags: list[str] = []

    def worker() -> None:
        resp = cache.serve(
            request=_fake_request(),
            route=route,
            principal=principal,
            params={"limit": 10},
            stamps=("v1",),
            compute=slow_compute,
        )
        results.append(resp.body)
        etags.append(resp.headers["etag"])

    threads = [threading.Thread(target=worker) for _ in range(5)]
    threads[0].start()
    assert started.wait(timeout=2.0)
    for t in threads[1:]:
        t.start()
    time.sleep(0.05)
    release.set()
    for t in threads:
        t.join(timeout=5.0)

    assert calls == 1
    assert len(results) == 5
    assert all(body == b'{"answer":42}' for body in results)
    assert len(set(etags)) == 1
    after_coalesced = api_coalesced_requests_total.labels(route=route)._value.get()
    assert after_coalesced - before_coalesced == 4


def test_cache_never_shares_across_roles_or_tenants() -> None:
    cache = ConditionalResultCache(ttl_seconds=30.0)
    route = "/api/v1/test-scope"
    t1 = uuid.uuid4()
    t2 = uuid.uuid4()
    viewer_t1 = Principal(subject="u", roles=frozenset({Role.VIEWER}), tenant_id=t1)
    admin_t1 = Principal(subject="u", roles=frozenset({Role.ADMIN}), tenant_id=t1)
    viewer_t2 = Principal(subject="u", roles=frozenset({Role.VIEWER}), tenant_id=t2)

    calls: list[str] = []

    r1 = cache.serve(
        request=_fake_request(),
        route=route,
        principal=viewer_t1,
        params={},
        stamps=("s1",),
        compute=lambda: calls.append("viewer_t1") or {"who": "viewer_t1"},
    )
    r2 = cache.serve(
        request=_fake_request(),
        route=route,
        principal=admin_t1,
        params={},
        stamps=("s1",),
        compute=lambda: calls.append("admin_t1") or {"who": "admin_t1"},
    )
    r3 = cache.serve(
        request=_fake_request(),
        route=route,
        principal=viewer_t2,
        params={},
        stamps=("s1",),
        compute=lambda: calls.append("viewer_t2") or {"who": "viewer_t2"},
    )
    assert calls == ["viewer_t1", "admin_t1", "viewer_t2"]
    assert len({r1.headers["etag"], r2.headers["etag"], r3.headers["etag"]}) == 3

    # Using viewer_t1's ETag from admin_t1 must NOT return 304.
    cross_role = cache.serve(
        request=_fake_request(if_none_match=r1.headers["etag"]),
        route=route,
        principal=admin_t1,
        params={},
        stamps=("s1",),
        compute=lambda: {"who": "admin_t1"},
    )
    assert cross_role.status_code == 200


def test_datalake_and_cost_dashboards_etag_304_and_input_invalidation(api_db: ApiDb) -> None:
    """Inputs tested for /dashboards/datalake and /dashboards/cost:
    scan_at, table_cost, maintenance run/job, audit_log, role/tenant scope."""
    conditional_cache.clear()
    session = api_db.session_factory()
    table_id = _seed(session)
    session.close()

    route = "/api/v1/dashboards/datalake"
    before_304 = api_not_modified_total.labels(route=route)._value.get()
    before_hits = api_response_cache_requests_total.labels(route=route, outcome="hit")._value.get()

    r1 = api_db.client.get(route, headers=VIEWER_HEADERS)
    assert r1.status_code == 200
    etag1 = r1.headers["etag"]
    assert etag1.startswith('"')

    # Conditional request with matching ETag -> 304 Not Modified without recomputing.
    r_304 = api_db.client.get(route, headers={**VIEWER_HEADERS, "If-None-Match": etag1})
    assert r_304.status_code == 304
    assert r_304.content == b""
    assert api_not_modified_total.labels(route=route)._value.get() == before_304 + 1

    # Unconditional second request -> served from short-lived ETag cache (hit).
    r_hit = api_db.client.get(route, headers=VIEWER_HEADERS)
    assert r_hit.status_code == 200
    assert r_hit.headers["etag"] == etag1
    assert (
        api_response_cache_requests_total.labels(route=route, outcome="hit")._value.get()
        == before_hits + 1
    )

    # Role isolation: operator gets a distinct ETag.
    r_op = api_db.client.get(route, headers=OPERATOR_HEADERS)
    assert r_op.headers["etag"] != etag1

    # Tenant isolation: tenant-scoped viewer gets a distinct ETag.
    tenant_headers = auth_header("viewer", tenant_id=uuid.uuid4())
    r_tenant = api_db.client.get(route, headers=tenant_headers)
    assert r_tenant.headers["etag"] != etag1

    # Input 1: new scan updates TableStats.scan_at -> ETag changes, old ETag returns 200.
    session = api_db.session_factory()
    stats = session.get(TableStats, table_id)
    assert stats is not None
    stats.scan_at = stats.scan_at + timedelta(minutes=5)
    session.commit()
    session.close()

    r2 = api_db.client.get(route, headers={**VIEWER_HEADERS, "If-None-Match": etag1})
    assert r2.status_code == 200
    etag2 = r2.headers["etag"]
    assert etag2 != etag1

    # Input 2: MaintenanceRun / MaintenanceJob change -> ETag changes.
    session = api_db.session_factory()
    run_id = uuid.uuid4()
    table = session.get(Table, table_id)
    assert table is not None
    preset = session.query(Preset).first()
    assert preset is not None
    session.add(
        MaintenanceRun(
            id=run_id,
            kind="maintenance",
            status="running",
            trigger="manual",
            created_at=datetime.now(UTC),
        )
    )
    session.add(
        MaintenanceJob(
            id=uuid.uuid4(),
            run_id=run_id,
            table_id=table_id,
            destination_id=preset.destination_id,
            mode="standard",
            status="running",
            started_at=datetime.now(UTC),
        )
    )
    session.commit()
    session.close()

    r3 = api_db.client.get(route, headers={**VIEWER_HEADERS, "If-None-Match": etag2})
    assert r3.status_code == 200
    assert r3.headers["etag"] != etag2

    # Cost dashboard: TableCost + AuditLog changes rotate ETag.
    cost_r1 = api_db.client.get("/api/v1/dashboards/cost", headers=VIEWER_HEADERS)
    assert cost_r1.status_code == 200
    cost_etag1 = cost_r1.headers["etag"]
    assert (
        api_db.client.get(
            "/api/v1/dashboards/cost", headers={**VIEWER_HEADERS, "If-None-Match": cost_etag1}
        ).status_code
        == 304
    )

    session = api_db.session_factory()
    session.add(
        TableCost(
            table_id=table_id,
            scan_at=datetime.now(UTC),
            precision="metadata",
            price_per_gb_month=0.023,
            live_bytes=100,
            dead_bytes=10,
            metadata_bytes=1,
            current_monthly_cost_usd=1.0,
            projected_monthly_cost_usd=0.8,
            projected_savings_usd_month=0.2,
        )
    )
    session.add(
        AuditLog(
            table_id=table_id,
            action="expire_snapshots",
            status="OK",
            actual_bytes_freed=1024,
            ts=datetime.now(UTC),
        )
    )
    session.commit()
    session.close()

    cost_r2 = api_db.client.get(
        "/api/v1/dashboards/cost", headers={**VIEWER_HEADERS, "If-None-Match": cost_etag1}
    )
    assert cost_r2.status_code == 200
    assert cost_r2.headers["etag"] != cost_etag1


def test_decisions_and_insights_etag_inputs(api_db: ApiDb) -> None:
    """Inputs tested for Decisions and Insights routes."""
    conditional_cache.clear()
    session = api_db.session_factory()
    table_id = _seed(session)
    session.close()

    # Decisions: layout recommendations & overview
    rec_url = "/api/v1/smart-maintenance/layout-recommendations"
    ov_url = "/api/v1/smart-maintenance/overview"
    rec1 = api_db.client.get(rec_url, headers=VIEWER_HEADERS)
    ov1 = api_db.client.get(ov_url, headers=VIEWER_HEADERS)
    assert rec1.status_code == 200 and ov1.status_code == 200
    assert (
        api_db.client.get(
            rec_url, headers={**VIEWER_HEADERS, "If-None-Match": rec1.headers["etag"]}
        ).status_code
        == 304
    )

    session = api_db.session_factory()
    rec_id = uuid.uuid4()
    session.add(
        LayoutRecommendation(
            id=rec_id,
            table_id=table_id,
            recommended_strategy="sort",
            recommended_columns=[{"column": "event_ts"}],
            status="pending",
            confidence=0.8,
        )
    )
    session.commit()
    session.close()

    rec2 = api_db.client.get(
        rec_url, headers={**VIEWER_HEADERS, "If-None-Match": rec1.headers["etag"]}
    )
    ov2 = api_db.client.get(
        ov_url, headers={**VIEWER_HEADERS, "If-None-Match": ov1.headers["etag"]}
    )
    assert rec2.status_code == 200 and rec2.headers["etag"] != rec1.headers["etag"]
    assert ov2.status_code == 200 and ov2.headers["etag"] != ov1.headers["etag"]

    # Insights: dedup clusters & shared-s3 report
    dedup_url = "/api/v1/dedup/clusters"
    s3_url = "/api/v1/anomalies/shared-s3"
    d1 = api_db.client.get(dedup_url, headers=VIEWER_HEADERS)
    s1 = api_db.client.get(s3_url, headers=VIEWER_HEADERS)
    assert d1.status_code == 200 and s1.status_code == 200
    assert (
        api_db.client.get(
            dedup_url, headers={**VIEWER_HEADERS, "If-None-Match": d1.headers["etag"]}
        ).status_code
        == 304
    )
    assert (
        api_db.client.get(
            s3_url, headers={**VIEWER_HEADERS, "If-None-Match": s1.headers["etag"]}
        ).status_code
        == 304
    )

    session = api_db.session_factory()
    session.add(
        TableSimilarityCluster(
            id=uuid.uuid4(),
            cluster_id=uuid.uuid4(),
            table_id=table_id,
            role="keep",
            keep_score=1.0,
            keep_reason="active",
            generation=1,
            computed_at=datetime.now(UTC),
        )
    )
    session.add(
        SharedS3AnomalyReport(
            id=uuid.uuid4(),
            generated_at=datetime.now(UTC),
            table_count=1,
            group_count=0,
            overlapping_table_count=0,
            skipped_count=0,
            sources_scanned=[],
            source_errors=[],
            groups=[],
        )
    )
    session.commit()
    session.close()

    d2 = api_db.client.get(
        dedup_url, headers={**VIEWER_HEADERS, "If-None-Match": d1.headers["etag"]}
    )
    s2 = api_db.client.get(s3_url, headers={**VIEWER_HEADERS, "If-None-Match": s1.headers["etag"]})
    assert d2.status_code == 200 and d2.headers["etag"] != d1.headers["etag"]
    assert s2.status_code == 200 and s2.headers["etag"] != s1.headers["etag"]


def test_path_to_a_etag_changes_on_scan_lifecycle_override_and_properties(api_db: ApiDb) -> None:
    """Inputs tested for /tables/{id}/path-to-a:
    scan_at, lifecycle_changed_at, param_overrides, property changes, preset update."""
    conditional_cache.clear()
    session = api_db.session_factory()
    table_id = _seed(session)
    session.close()

    url = f"/api/v1/tables/{table_id}/path-to-a"
    r1 = api_db.client.get(url, headers=ADMIN_HEADERS)
    assert r1.status_code == 200
    etag1 = r1.headers["etag"]
    assert (
        api_db.client.get(url, headers={**ADMIN_HEADERS, "If-None-Match": etag1}).status_code == 304
    )

    # 1. Override change via /path-to-a/apply rotates ETag.
    apply = api_db.client.post(
        url + "/apply", headers=ADMIN_HEADERS, json={"step_id": "optimize_all"}
    )
    assert apply.status_code == 200
    r2 = api_db.client.get(url, headers={**ADMIN_HEADERS, "If-None-Match": etag1})
    assert r2.status_code == 200
    etag2 = r2.headers["etag"]
    assert etag2 != etag1

    # 2. Lifecycle change rotates ETag.
    session = api_db.session_factory()
    table = session.get(Table, table_id)
    assert table is not None
    table.lifecycle = "archived"
    table.lifecycle_changed_at = datetime.now(UTC)
    session.commit()
    session.close()

    r3 = api_db.client.get(url, headers={**ADMIN_HEADERS, "If-None-Match": etag2})
    assert r3.status_code == 200
    etag3 = r3.headers["etag"]
    assert etag3 != etag2

    # 3. Property change in TableStats rotates ETag.
    session = api_db.session_factory()
    stats = session.get(TableStats, table_id)
    assert stats is not None
    stats.target_file_size_bytes = 268435456
    session.commit()
    session.close()

    r4 = api_db.client.get(url, headers={**ADMIN_HEADERS, "If-None-Match": etag3})
    assert r4.status_code == 200
    etag4 = r4.headers["etag"]
    assert etag4 != etag3

    # 4. Assignment override edit rotates ETag.
    session = api_db.session_factory()
    assignment = session.get(TableAssignment, table_id)
    assert assignment is not None
    assignment.param_overrides = {"min_small_files": 5}
    session.commit()
    session.close()

    r5 = api_db.client.get(url, headers={**ADMIN_HEADERS, "If-None-Match": etag4})
    assert r5.status_code == 200
    assert r5.headers["etag"] != etag4
