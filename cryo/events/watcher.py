from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from cryo.events.snapshots import SnapshotObservation, SummaryDeltas

logger = logging.getLogger(__name__)


class SnapshotProbe(Protocol):
    def current_snapshot_id(self, table_ref: str) -> int | None: ...


@runtime_checkable
class CommitObservingProbe(Protocol):
    """A probe that can also say what was committed since the last snapshot it saw."""

    def observe(
        self,
        table_ref: str,
        *,
        last_seen_snapshot_id: int | None,
        last_seen_sequence_number: int | None,
    ) -> SnapshotObservation | None: ...


@dataclass(frozen=True)
class TableCommitted:
    table_ref: str
    snapshot_id: int
    observed_at: datetime
    parent_snapshot_id: int | None = None
    files_added: int = 0
    files_removed: int = 0
    bytes_added: int = 0
    bytes_removed: int = 0
    delete_files_added: int = 0
    equality_delete_files_added: int = 0
    position_delete_files_added: int = 0
    delete_files_removed: int = 0
    equality_delete_files_removed: int = 0
    position_delete_files_removed: int = 0
    snapshots_added: int = 0
    table_id: str | None = None
    sequence_number: int | None = None
    committed_at: datetime | None = None
    # Commits in snapshots_added that a maintenance job made (compaction's "replace").
    maintenance_commits: int = 0
    # False when the deltas undercount: the walk to the previous snapshot stopped early.
    complete: bool = True


@dataclass
class _WatchState:
    table_id: str | None = None
    snapshot_id: int | None = None
    sequence_number: int | None = None
    next_probe_at: float = 0.0


class TableCommitWatcher:
    """Poll current-snapshot-id for hot tables and emit commit events.

    Bounded per poll: at most ``max_tables_per_poll`` catalog probes, and a table is not
    re-probed within ``min_table_interval_seconds``. Tables wait their turn oldest-probed
    first, so a large watch list is swept round-robin instead of loading every table every
    poll.
    """

    def __init__(
        self,
        probe: SnapshotProbe | CommitObservingProbe,
        publish: Callable[[TableCommitted], None],
        *,
        poll_interval_seconds: float = 5.0,
        hot_table_refs: set[str] | None = None,
        max_tables_per_poll: int | None = None,
        min_table_interval_seconds: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._probe = probe
        self._publish = publish
        self._poll_interval_seconds = poll_interval_seconds
        self._max_tables_per_poll = max_tables_per_poll
        self._min_table_interval_seconds = min_table_interval_seconds
        self._clock = clock
        self._state: dict[str, _WatchState] = {}
        for table_ref in hot_table_refs or set():
            self.watch(table_ref)

    @property
    def watched(self) -> frozenset[str]:
        return frozenset(self._state)

    def watch(
        self,
        table_ref: str,
        *,
        table_id: str | None = None,
        snapshot_id: int | None = None,
        sequence_number: int | None = None,
    ) -> None:
        """Start watching. A known ``snapshot_id`` (the last one Cryo applied) lets the first
        probe report commits made while the watcher was down instead of only seeding."""
        state = self._state.get(table_ref)
        if state is None:
            self._state[table_ref] = _WatchState(
                table_id=table_id, snapshot_id=snapshot_id, sequence_number=sequence_number
            )
            return
        if table_id is not None:
            state.table_id = table_id

    def unwatch(self, table_ref: str) -> None:
        self._state.pop(table_ref, None)

    def poll_once(self, *, now: datetime | None = None) -> list[TableCommitted]:
        from cryo.observability.metrics import events_snapshot_probes_total

        current = now or datetime.now(UTC)
        tick = self._clock()
        due = sorted(
            (state.next_probe_at, table_ref)
            for table_ref, state in self._state.items()
            if state.next_probe_at <= tick
        )
        budget = self._max_tables_per_poll
        if budget is not None and len(due) > budget:
            events_snapshot_probes_total.labels(outcome="skipped_budget").inc(len(due) - budget)
            due = due[:budget]
        events: list[TableCommitted] = []
        for _, table_ref in due:
            state = self._state[table_ref]
            state.next_probe_at = tick + self._min_table_interval_seconds
            try:
                event = self._probe_table(table_ref, state, current)
            except Exception as exc:
                events_snapshot_probes_total.labels(outcome="failed").inc()
                logger.warning("Snapshot probe failed for %s: %s", table_ref, exc)
                continue
            if event is None:
                events_snapshot_probes_total.labels(outcome="unchanged").inc()
                continue
            events_snapshot_probes_total.labels(outcome="committed").inc()
            self._publish(event)
            events.append(event)
        return events

    def _probe_table(
        self, table_ref: str, state: _WatchState, current: datetime
    ) -> TableCommitted | None:
        previous = state.snapshot_id
        if isinstance(self._probe, CommitObservingProbe):
            observation = self._probe.observe(
                table_ref,
                last_seen_snapshot_id=previous,
                last_seen_sequence_number=state.sequence_number,
            )
        else:
            snapshot_id = self._probe.current_snapshot_id(table_ref)
            observation = (
                SnapshotObservation(snapshot_id=snapshot_id) if snapshot_id is not None else None
            )
        if observation is None:
            return None
        state.snapshot_id = observation.snapshot_id
        latest = observation.latest
        if latest is not None and latest.sequence_number is not None:
            state.sequence_number = latest.sequence_number
        if previous is None or observation.snapshot_id == previous:
            return None
        return _event_from_observation(
            table_ref, state.table_id, observation, previous=previous, observed_at=current
        )

    def run_loop(self, *, stop: Callable[[], bool] | None = None) -> None:
        while stop is None or not stop():
            self.poll_once()
            time.sleep(self._poll_interval_seconds)


def _event_from_observation(
    table_ref: str,
    table_id: str | None,
    observation: SnapshotObservation,
    *,
    previous: int,
    observed_at: datetime,
) -> TableCommitted:
    from cryo.observability.metrics import events_commits_observed_total

    deltas = observation.deltas() if observation.commits else SummaryDeltas()
    latest = observation.latest
    if deltas.snapshots_added:
        writer_commits = deltas.snapshots_added - deltas.maintenance_commits
        if writer_commits:
            events_commits_observed_total.labels(kind="writer").inc(writer_commits)
        if deltas.maintenance_commits:
            events_commits_observed_total.labels(kind="maintenance").inc(deltas.maintenance_commits)
    committed_at = (
        datetime.fromtimestamp(latest.timestamp_ms / 1000, UTC)
        if latest is not None and latest.timestamp_ms
        else None
    )
    # With no commit chain (a probe that only knows the current id) this stays a
    # watermark-only event: apply_commit_delta records the commit without pretending the
    # table was re-measured (issue #188).
    return TableCommitted(
        table_ref=table_ref,
        snapshot_id=observation.snapshot_id,
        observed_at=observed_at,
        parent_snapshot_id=previous,
        files_added=deltas.files_added,
        files_removed=deltas.files_removed,
        bytes_added=deltas.bytes_added,
        bytes_removed=deltas.bytes_removed,
        delete_files_added=deltas.delete_files_added,
        equality_delete_files_added=deltas.equality_delete_files_added,
        position_delete_files_added=deltas.position_delete_files_added,
        delete_files_removed=deltas.delete_files_removed,
        equality_delete_files_removed=deltas.equality_delete_files_removed,
        position_delete_files_removed=deltas.position_delete_files_removed,
        snapshots_added=deltas.snapshots_added,
        table_id=table_id,
        sequence_number=latest.sequence_number if latest is not None else None,
        committed_at=committed_at,
        maintenance_commits=deltas.maintenance_commits,
        complete=observation.complete and bool(observation.commits),
    )


class InMemorySnapshotProbe:
    def __init__(self, snapshots: Mapping[str, int] | None = None) -> None:
        self.snapshots = dict(snapshots or {})

    def current_snapshot_id(self, table_ref: str) -> int | None:
        return self.snapshots.get(table_ref)

    def commit(self, table_ref: str, snapshot_id: int) -> None:
        self.snapshots[table_ref] = snapshot_id
