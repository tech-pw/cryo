"""Which build each running Cryo component is on, and whether they agree (#489).

Every long-running process (api, worker, governor, scheduler, scanner, events) writes its
build to Redis on start and refreshes it in the background, so the API can list the whole
install without Kubernetes access. Records expire on their own once a pod is gone.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
from datetime import UTC, datetime
from typing import Any

from cryo.build_info import build_info
from cryo.observability.metrics import component_build_info

logger = logging.getLogger(__name__)

_KEY_PREFIX = "cryo:component_build:"
_TTL_SECONDS = 600
_REFRESH_SECONDS = 120
_MAX_RECORDS = 500
_started: set[str] = set()


def _env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def component_record(component: str) -> dict[str, Any]:
    info = build_info()
    return {
        "component": component,
        "instance": socket.gethostname(),
        "image": _env("CRYO_IMAGE"),
        "version": info.version,
        "git_sha": info.git_sha,
        "built_at": info.built_at,
        "spark_image": _env("CRYO_SPARK_K8S_IMAGE"),
        "native_image": _env("CRYO_NATIVE_IMAGE"),
        "reported_at": datetime.now(UTC).isoformat(),
    }


def report_component(component: str, *, redis_client: Any = None) -> bool:
    record = component_record(component)
    component_build_info.labels(
        component=component, commit=record["git_sha"] or "unknown", version=record["version"]
    ).set(1)
    if redis_client is None:
        from cryo.queue.redis_queue import get_redis_client

        redis_client = get_redis_client()
    if redis_client is None:
        return False
    try:
        redis_client.set(
            f"{_KEY_PREFIX}{component}:{record['instance']}", json.dumps(record), ex=_TTL_SECONDS
        )
    except Exception:  # noqa: BLE001 - build reporting must never take a component down
        logger.debug("Could not report %s build to Redis", component, exc_info=True)
        return False
    return True


def start_component_reporter(component: str) -> None:
    """Report now, then refresh in a daemon thread. Safe to call more than once."""
    if component in _started:
        return
    _started.add(component)
    report_component(component)
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(_REFRESH_SECONDS):
            report_component(component)

    threading.Thread(target=loop, name=f"component-build-{component}", daemon=True).start()


def list_component_builds(*, redis_client: Any = None) -> list[dict[str, Any]]:
    if redis_client is None:
        from cryo.queue.redis_queue import get_redis_client

        redis_client = get_redis_client()
    if redis_client is None:
        return []
    records: list[dict[str, Any]] = []
    try:
        keys = list(redis_client.scan_iter(match=f"{_KEY_PREFIX}*", count=200))[:_MAX_RECORDS]
        for raw in redis_client.mget(keys) if keys else []:
            if not raw:
                continue
            try:
                records.append(json.loads(raw))
            except (TypeError, ValueError):
                continue
    except Exception:  # noqa: BLE001 - an unreachable Redis shows as "no reports"
        logger.warning("Could not read component builds from Redis", exc_info=True)
        return []
    return sorted(records, key=lambda r: (str(r.get("component")), str(r.get("instance"))))


def build_skew(records: list[dict[str, Any]]) -> bool:
    """True when the reporting components are not all on one commit (or version, without SHAs)."""
    shas = {r.get("git_sha") for r in records if r.get("git_sha")}
    if shas:
        return len(shas) > 1
    return len({r.get("version") for r in records if r.get("version")}) > 1
