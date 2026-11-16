"""The worker's live writer-window gate (cadence math in cryo/planner/writer_window.py).

Before each data-rewrite statement the worker reads the table's snapshot list from the
catalog (one ``load_table``; no manifest or data file is read) and asks whether the table's
external writer leaves room for the statement to commit. The scanner's stored commit log is
up to a day old and cannot say where in its cycle the writer is right now.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from cryo.planner.writer_window import (
    WriterCommit,
    WriterWindow,
    WriterWindowPolicy,
    decide_writer_window,
    writer_cadence,
)

if TYPE_CHECKING:
    from cryo.events.service import LiveCatalogSnapshotProbe

logger = logging.getLogger(__name__)

WriterCommitReader = Callable[[Mapping[str, Any]], Sequence[WriterCommit]]


class LiveWriterCommitReader:
    """The table's commits, read live from its catalog."""

    def __init__(self, probe: LiveCatalogSnapshotProbe) -> None:
        self._probe = probe

    def __call__(self, stats: Mapping[str, Any]) -> list[WriterCommit]:
        from cryo.events.service import WatchTarget

        source_id = stats.get("source_id")
        schema_name = stats.get("schema_name")
        table_name = stats.get("table_name")
        if not (source_id and schema_name and table_name):
            raise LookupError("table stats carry no source, schema and table name")
        target = WatchTarget(
            table_id=str(stats.get("table_id") or ""),
            source_id=str(source_id),
            schema_name=str(schema_name),
            table_name=str(table_name),
            table_ref=str(stats.get("table_ref") or f"{schema_name}.{table_name}"),
        )
        return [
            WriterCommit(commit.timestamp_ms, commit.operation)
            for commit in self._probe.snapshot_commits(target)
            if commit.timestamp_ms is not None
        ]


def build_live_writer_commit_reader(*, timeout_seconds: float) -> LiveWriterCommitReader:
    from cryo.db.session import SessionLocal
    from cryo.events.service import LiveCatalogSnapshotProbe

    return LiveWriterCommitReader(
        LiveCatalogSnapshotProbe(SessionLocal, timeout_seconds=timeout_seconds)
    )


def check_writer_window(
    reader: WriterCommitReader,
    stats: Mapping[str, Any],
    params: Mapping[str, Any],
    *,
    now_ms: int,
) -> WriterWindow | None:
    """The window verdict for a rewrite starting now; None when the preset turns it off.

    Raises whatever the reader raises; the caller decides how to fail.
    """
    policy = WriterWindowPolicy.from_params(params)
    if not policy.enabled:
        return None
    commits = reader(stats)
    cadence = writer_cadence(commits, now_ms=now_ms, policy=policy)
    return decide_writer_window(cadence, now_ms=now_ms, policy=policy)
