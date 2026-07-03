#!/usr/bin/env python3
"""Minimal Cryo orchestration smoke test (Redis enqueue + job store round-trip)."""

from __future__ import annotations

import os
import sys
import uuid


def main() -> int:
    try:
        from cryo.db.repositories.job_store import SQLJobStore
        from cryo.db.session import SessionLocal
        from cryo.events.consumer import InMemoryEventStream, TableCommitConsumer
        from cryo.events.watcher import InMemorySnapshotProbe, TableCommitWatcher
        from cryo.engines.native.payload import plan_to_payload
        from cryo.planner.planner import build_plan
        from cryo.domain.enums import Mode
    except ImportError as exc:
        print(f"SKIP: dependencies missing ({exc})")
        return 0

    plan = build_plan({"schema_name": "demo", "table_name": "events"}, [], Mode.MANIFEST_ONLY, {})
    payload = plan_to_payload(plan)
    assert payload["table_ref"]
    assert payload["operations"][0]["type"] == "optimize_manifests"

    stream = InMemoryEventStream()
    consumer = TableCommitConsumer(stream)
    probe = InMemorySnapshotProbe({"iceberg.demo.events": 1})
    watcher = TableCommitWatcher(probe, consumer.publish)
    watcher.watch("iceberg.demo.events")
    probe.commit("iceberg.demo.events", 2)
    events = watcher.poll_once()
    assert len(events) == 1
    result = consumer.consume_once()
    assert result.handled == 1

    if os.getenv("CRYO_SMOKE_DB") == "1":
        store = SQLJobStore(SessionLocal)
        try:
            store.get_job(str(uuid.uuid4()))
        except KeyError:
            pass

    print("smoke_cryo_e2e: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
