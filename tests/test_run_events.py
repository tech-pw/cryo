from __future__ import annotations

import uuid

from cryo.queue.run_events import (
    InMemoryRunEventBus,
    NullRunEventSink,
    build_log_line,
    safe_publish,
    scan_progress_message,
)


def test_in_memory_run_event_bus_replay_and_stream() -> None:
    bus = InMemoryRunEventBus()
    run_id = uuid.uuid4()
    event = build_log_line(run_id=run_id, message="hello")
    bus.publish(run_id, event)

    replayed = bus.replay(run_id)
    assert len(replayed) == 1
    assert replayed[0]["message"] == "hello"

    bus.publish(run_id, build_log_line(run_id=run_id, message="second"))
    rows = bus.read_blocking(run_id, last_id=bus.latest_message_id(run_id))
    assert rows == []


def test_null_sink_is_noop() -> None:
    sink = NullRunEventSink()
    safe_publish(sink, uuid.uuid4(), build_log_line(run_id=uuid.uuid4(), message="ignored"))


def test_scan_progress_message_formats() -> None:
    assigned = scan_progress_message(
        {"event": "assigned", "discovered": 10, "assigned": 4},
        chunk_id=0,
        num_chunks=2,
    )
    assert "Assigned 4 of 10" in assigned

    started = scan_progress_message(
        {"event": "start_table", "table_ref": "demo.events"},
        chunk_id=1,
        num_chunks=3,
    )
    assert started == "Scanning demo.events"
