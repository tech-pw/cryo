"""Component build reporting and version skew (#489)."""

from __future__ import annotations

import fakeredis
import pytest

from cryo.component_builds import build_skew, list_component_builds, report_component


@pytest.fixture()
def redis() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis()


def test_report_then_list_round_trips(redis, monkeypatch) -> None:
    monkeypatch.setenv("CRYO_GIT_SHA", "abc123")
    monkeypatch.setenv("CRYO_IMAGE", "registry.example/cryo@sha256:" + "a" * 64)
    monkeypatch.setenv("CRYO_SPARK_K8S_IMAGE", "registry.example/cryo-spark@sha256:" + "b" * 64)

    assert report_component("worker", redis_client=redis)
    records = list_component_builds(redis_client=redis)

    assert len(records) == 1
    assert records[0]["component"] == "worker"
    assert records[0]["git_sha"] == "abc123"
    assert records[0]["image"].startswith("registry.example/cryo@")
    assert records[0]["spark_image"].startswith("registry.example/cryo-spark@")
    assert 0 < redis.ttl(next(iter(redis.scan_iter()))) <= 600


def test_skew_compares_commits_then_versions() -> None:
    assert not build_skew([{"git_sha": "a", "version": "1"}, {"git_sha": "a", "version": "1"}])
    assert build_skew([{"git_sha": "a"}, {"git_sha": "b"}])
    assert build_skew([{"git_sha": None, "version": "1"}, {"git_sha": None, "version": "2"}])
    assert not build_skew([])


def test_no_redis_lists_nothing() -> None:
    class Broken:
        def scan_iter(self, **_kwargs):
            raise ConnectionError("down")

    assert list_component_builds(redis_client=Broken()) == []


def test_components_endpoint(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from cryo.api.app import app
    from tests.auth_helpers import auth_header

    redis = fakeredis.FakeRedis()
    monkeypatch.setenv("CRYO_GIT_SHA", "abc123")
    report_component("api", redis_client=redis)
    monkeypatch.setenv("CRYO_GIT_SHA", "def456")
    report_component("worker", redis_client=redis)
    monkeypatch.setattr("cryo.queue.redis_queue.get_redis_client", lambda **_: redis)

    response = TestClient(app).get("/api/v1/version/components", headers=auth_header("viewer"))

    assert response.status_code == 200
    body = response.json()
    assert [c["component"] for c in body["components"]] == ["api", "worker"]
    assert body["skew"] is True
