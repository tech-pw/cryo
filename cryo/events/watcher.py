from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol


class SnapshotProbe(Protocol):
    def current_snapshot_id(self, table_ref: str) -> int | None: ...


@dataclass(frozen=True)
class TableCommitted:
    table_ref: str
    snapshot_id: int
    observed_at: datetime


class TableCommitWatcher:
    """Poll current-snapshot-id for hot tables and emit commit events."""

    def __init__(
        self,
        probe: SnapshotProbe,
        publish: Callable[[TableCommitted], None],
        *,
        poll_interval_seconds: float = 5.0,
        hot_table_refs: set[str] | None = None,
    ) -> None:
        self._probe = probe
        self._publish = publish
        self._poll_interval_seconds = poll_interval_seconds
        self._hot_table_refs = hot_table_refs or set()
        self._last_seen: dict[str, int] = {}

    def watch(self, table_ref: str) -> None:
        self._hot_table_refs.add(table_ref)

    def unwatch(self, table_ref: str) -> None:
        self._hot_table_refs.discard(table_ref)

    def poll_once(self, *, now: datetime | None = None) -> list[TableCommitted]:
        current = now or datetime.now(UTC)
        events: list[TableCommitted] = []
        for table_ref in sorted(self._hot_table_refs):
            snapshot_id = self._probe.current_snapshot_id(table_ref)
            if snapshot_id is None:
                continue
            previous = self._last_seen.get(table_ref)
            self._last_seen[table_ref] = snapshot_id
            if previous is not None and snapshot_id != previous:
                event = TableCommitted(
                    table_ref=table_ref, snapshot_id=snapshot_id, observed_at=current
                )
                self._publish(event)
                events.append(event)
        return events

    def run_loop(self, *, stop: Callable[[], bool] | None = None) -> None:
        while stop is None or not stop():
            self.poll_once()
            time.sleep(self._poll_interval_seconds)


class InMemorySnapshotProbe:
    def __init__(self, snapshots: Mapping[str, int] | None = None) -> None:
        self.snapshots = dict(snapshots or {})

    def current_snapshot_id(self, table_ref: str) -> int | None:
        return self.snapshots.get(table_ref)

    def commit(self, table_ref: str, snapshot_id: int) -> None:
        self.snapshots[table_ref] = snapshot_id
