"""Parse the result lines a lane pod writes to its log (see cryo_spark_runner._run_manifest).

The pod log is the only channel back from the Spark pod: the Spark image carries no database
driver or Cryo code, and giving it write access to Postgres would widen what a compromised
maintenance pod could touch. Each executed or skipped statement prints exactly one line with
a fixed prefix, so the coordinator can re-read a growing log and pick up where it left off.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

RESULT_PREFIX = "[cryo-result] "
TABLE_START_PREFIX = "[cryo-table-start] "
LANE_DONE_PREFIX = "[cryo-lane-done] "


@dataclass(frozen=True)
class StatementResult:
    table_id: uuid.UUID
    seq: int
    action: str
    ok: bool
    skipped: bool = False
    duration_ms: int | None = None
    rows: int | None = None
    error: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> tuple[uuid.UUID, int]:
        return (self.table_id, self.seq)


@dataclass(frozen=True)
class LaneLog:
    results: list[StatementResult]
    started_tables: list[uuid.UUID]
    lane_done: dict[str, Any] | None


def _parse_result(raw: str) -> StatementResult | None:
    try:
        payload = json.loads(raw)
        table_id = uuid.UUID(str(payload["table_id"]))
        seq = int(payload["seq"])
        action = str(payload["action"])
    except (ValueError, KeyError, TypeError):
        return None
    metrics = payload.get("metrics")
    return StatementResult(
        table_id=table_id,
        seq=seq,
        action=action,
        ok=bool(payload.get("ok")),
        skipped=bool(payload.get("skipped", False)),
        duration_ms=payload.get("duration_ms"),
        rows=payload.get("rows"),
        error=payload.get("error"),
        metrics=metrics if isinstance(metrics, dict) else {},
    )


def parse_lane_log(text: str) -> LaneLog:
    results: list[StatementResult] = []
    started: list[uuid.UUID] = []
    lane_done: dict[str, Any] | None = None
    # Split on "\n" only: str.splitlines() also breaks on U+0085/U+2028/U+2029, which can
    # appear unescaped inside a JSON string (an error message quoting row data) and would
    # shred a valid line. Same trap that discarded successful scans in go_scan.py.
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith(RESULT_PREFIX):
            parsed = _parse_result(line[len(RESULT_PREFIX) :])
            if parsed is not None:
                results.append(parsed)
        elif line.startswith(TABLE_START_PREFIX):
            try:
                started.append(
                    uuid.UUID(str(json.loads(line[len(TABLE_START_PREFIX) :])["table_id"]))
                )
            except (ValueError, KeyError, TypeError):
                continue
        elif line.startswith(LANE_DONE_PREFIX):
            try:
                done = json.loads(line[len(LANE_DONE_PREFIX) :])
            except ValueError:
                continue
            if isinstance(done, dict):
                lane_done = done
    return LaneLog(results=results, started_tables=started, lane_done=lane_done)
