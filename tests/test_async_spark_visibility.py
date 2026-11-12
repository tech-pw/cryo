"""Parked async Spark jobs are visible to the UI (#491)."""

from __future__ import annotations

import fakeredis
import pytest
from fastapi.testclient import TestClient

from cryo.queue.async_spark import (
    AsyncSparkEntry,
    InMemoryAsyncSparkRegistry,
    RedisAsyncSparkRegistry,
)
from tests.auth_helpers import auth_header


def _entry(job_id: str, parked_at: float = 100.0) -> AsyncSparkEntry:
    return AsyncSparkEntry(
        job_id=job_id,
        table_id="t",
        destination_id="d",
        step_index=1,
        owner="worker-1",
        spark={"ref": f"cryo-spark-{job_id}"},
        parked_at=parked_at,
    )


@pytest.fixture(params=["memory", "redis"])
def registry(request):
    if request.param == "memory":
        return InMemoryAsyncSparkRegistry()
    return RedisAsyncSparkRegistry(fakeredis.FakeRedis())


def test_describe_reports_park_check_and_due_times(registry) -> None:
    registry.park(_entry("a"), next_check_at=130.0)
    registry.park(_entry("b"), next_check_at=500.0)
    registry.mark_checked(["a"], 120.0)

    found = registry.describe(["a", "b", "missing"])

    assert set(found) == {"a", "b"}
    assert found["a"].spark_ref == "cryo-spark-a"
    assert found["a"].parked_at == 100.0
    assert found["a"].last_checked_at == 120.0
    assert found["a"].next_check_at == 130.0
    assert found["b"].last_checked_at is None


def test_overdue_counts_checks_due_before_the_cutoff(registry) -> None:
    registry.park(_entry("a"), next_check_at=100.0)
    registry.park(_entry("b"), next_check_at=500.0)

    assert registry.overdue_count(200.0) == 1


def test_clear_drops_the_check_time(registry) -> None:
    registry.park(_entry("a"), next_check_at=100.0)
    registry.mark_checked(["a"], 90.0)
    registry.clear("a")
    registry.park(_entry("a"), next_check_at=100.0)

    assert registry.describe(["a"])["a"].last_checked_at is None


def test_endpoints(monkeypatch) -> None:
    from cryo.api import routes_async_spark
    from cryo.api.app import app

    registry = RedisAsyncSparkRegistry(fakeredis.FakeRedis())
    registry.park(_entry("late"), next_check_at=1.0)
    registry.park(_entry("fresh"), next_check_at=9_999_999_999.0)
    monkeypatch.setattr(routes_async_spark, "_registry", lambda: registry)
    client = TestClient(app)

    summary = client.get("/api/v1/async-spark/summary", headers=auth_header("viewer")).json()
    assert summary["parked"] == 2
    assert summary["overdue"] == 1

    jobs = client.post(
        "/api/v1/async-spark/jobs",
        json={"job_ids": ["late", "fresh", "other"]},
        headers=auth_header("viewer"),
    ).json()["jobs"]
    assert {j["job_id"]: j["overdue"] for j in jobs} == {"late": True, "fresh": False}
