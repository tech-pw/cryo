"""Observation Lakehouse (SANER 2026, arXiv:2512.02795).

Tall, append-only local archive of every actuation (stimulus, response, context) emitted by Cryo.
Sits next to ``RunEvent`` (Redis Streams, capped) and ``AuditLog`` (Postgres, transactional) as
the long-term store -- streams evict after a few hundred rows and Postgres keeps only summarised
audit rows, so this file-backed archive is the place where high-cardinality diagnostic history
lives.

Storage format: one file per UTC hour, named ``_events-YYYYMMDDHH.jsonl``. Each file is
plain JSONL -- one JSON record per line. This avoids a pyarrow dependency and any tool that
reads the extension will get valid JSONL.

Concurrency: appends are serialized through a process-wide ``threading.Lock`` (good enough for
the FastAPI worker pool) and, when ``fcntl`` is available, an advisory ``flock`` on the file
descriptor so concurrent processes don't interleave writes either.
"""

from __future__ import annotations

import errno
import fcntl
import json
import logging
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

_LEVEL = Literal["debug", "info", "warning", "error", "critical"]

Payload = dict[str, Any]


@dataclass
class ObservationEvent:
    """One tall row in the observation archive."""

    id: uuid.UUID
    recorded_at: datetime
    run_id: str | None
    event_type: str
    level: str
    table_id: str | None
    subject: str
    payload: Payload
    correlation_id: str | None = None
    actor: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ObservationEvent:
        recorded = raw.get("recorded_at")
        if isinstance(recorded, str):
            recorded_dt = _parse_iso(recorded)
        elif isinstance(recorded, datetime):
            recorded_dt = recorded
        else:
            recorded_dt = datetime.now(UTC)
        event_id = raw.get("id")
        if event_id is None or event_id == "":
            event_id = uuid.uuid4()
        elif isinstance(event_id, str):
            event_id = uuid.UUID(event_id)
        payload = raw.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {"value": payload}
        return cls(
            id=event_id,
            recorded_at=recorded_dt,
            run_id=raw.get("run_id"),
            event_type=str(raw.get("event_type") or raw.get("type") or "unknown"),
            level=str(raw.get("level") or "info"),
            table_id=raw.get("table_id"),
            subject=str(raw.get("subject") or raw.get("message") or ""),
            payload=payload,
            correlation_id=raw.get("correlation_id"),
            actor=raw.get("actor"),
        )


@dataclass
class ObservationLakehouse:
    """File-backed observation archive, one JSONL file per UTC hour."""

    storage_path: str = ".local/observation_lakehouse"
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        self._root = Path(self.storage_path).expanduser()
        self._root.mkdir(parents=True, exist_ok=True)

    # ----- file naming -----

    def _file_for(self, moment: datetime) -> Path:
        return self._root / f"_events-{moment.strftime('%Y%m%d%H')}.jsonl"

    def _all_files(self) -> list[Path]:
        if not self._root.exists():
            return []
        return sorted(self._root.glob("_events-*.jsonl"))

    # ----- writers -----

    def append(self, event: ObservationEvent) -> None:
        record = {
            "id": str(event.id),
            "recorded_at": event.recorded_at.astimezone(UTC).isoformat(),
            "run_id": event.run_id,
            "event_type": event.event_type,
            "level": event.level,
            "table_id": event.table_id,
            "subject": event.subject,
            "payload": event.payload,
            "correlation_id": event.correlation_id,
            "actor": event.actor,
        }
        target = self._file_for(event.recorded_at.astimezone(UTC))
        line = json.dumps(record, sort_keys=True, separators=(",", ":"), default=_json_default)
        with self._lock:
            try:
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
            except OSError as exc:
                if exc.errno == errno.ENOENT:
                    self._root.mkdir(parents=True, exist_ok=True)
                    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
                else:
                    raise
            try:
                if fcntl is not None:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX)
                    except OSError:
                        logger.debug("flock unavailable for %s; relying on thread lock", target)
                os.write(fd, (line + "\n").encode("utf-8"))
            finally:
                if fcntl is not None:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                    except OSError:
                        pass
                os.close(fd)

    def append_dict(self, payload: dict[str, Any]) -> ObservationEvent:
        event = ObservationEvent.from_dict(payload)
        self.append(event)
        return event

    # ----- readers -----

    def query(
        self,
        table_id: str | None = None,
        event_type: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
    ) -> list[ObservationEvent]:
        if limit <= 0:
            return []
        since_utc = since.astimezone(UTC) if since is not None else None
        until_utc = until.astimezone(UTC) if until is not None else None
        matches: list[ObservationEvent] = []
        for path in self._all_files():
            since_marker = (
                f"_events-{since_utc.strftime('%Y%m%d%H')}.jsonl" if since_utc is not None else None
            )
            until_marker = (
                f"_events-{until_utc.strftime('%Y%m%d%H')}.jsonl" if until_utc is not None else None
            )
            if since_marker is not None and path.name < since_marker:
                continue
            if until_marker is not None and path.name > until_marker:
                continue
            for event in _read_file(path):
                if table_id is not None and event.table_id != table_id:
                    continue
                if event_type is not None and event.event_type != event_type:
                    continue
                if since_utc is not None and event.recorded_at < since_utc:
                    continue
                if until_utc is not None and event.recorded_at > until_utc:
                    continue
                matches.append(event)
                if len(matches) >= limit:
                    return matches
        return matches

    # ----- maintenance -----

    def compact(self) -> dict[str, int]:
        """Deduplicate by id, rewriting each hour file in place. Tiny files are not merged across
        hour buckets -- Iceberg would partition exactly that way, and merging across hours would
        defeat the "files are cheap to skip" property of hourly partitioning."""
        files_merged = 0
        rows = 0
        bytes_in = 0
        bytes_out = 0
        with self._lock:
            for path in self._all_files():
                bytes_in += path.stat().st_size
                seen: dict[str, ObservationEvent] = {}
                for event in _read_file(path):
                    seen[str(event.id)] = event
                tmp = path.with_suffix(path.suffix + ".tmp")
                with tmp.open("w", encoding="utf-8") as fh:
                    for event in seen.values():
                        record = {
                            "id": str(event.id),
                            "recorded_at": event.recorded_at.astimezone(UTC).isoformat(),
                            "run_id": event.run_id,
                            "event_type": event.event_type,
                            "level": event.level,
                            "table_id": event.table_id,
                            "subject": event.subject,
                            "payload": event.payload,
                            "correlation_id": event.correlation_id,
                            "actor": event.actor,
                        }
                        fh.write(
                            json.dumps(
                                record, sort_keys=True, separators=(",", ":"), default=_json_default
                            )
                            + "\n"
                        )
                        rows += 1
                tmp.replace(path)
                bytes_out += path.stat().st_size
                files_merged += 1
        return {
            "files_merged": files_merged,
            "rows": rows,
            "bytes_in": bytes_in,
            "bytes_out": bytes_out,
        }

    def archive_stats(self) -> dict[str, Any]:
        total_bytes = 0
        total_files = 0
        oldest: datetime | None = None
        newest: datetime | None = None
        for path in self._all_files():
            total_files += 1
            total_bytes += path.stat().st_size
            for event in _read_file(path):
                if oldest is None or event.recorded_at < oldest:
                    oldest = event.recorded_at
                if newest is None or event.recorded_at > newest:
                    newest = event.recorded_at
        return {
            "total_files": total_files,
            "total_bytes": total_bytes,
            "oldest_event": oldest,
            "newest_event": newest,
        }

    def reset(self) -> None:
        """Delete every archive file. Used by tests; not exposed via the API."""
        with self._lock:
            for path in self._all_files():
                path.unlink()


# ----- module-level helpers -----


_DEFAULT_LAKEHOUSE: ObservationLakehouse | None = None
_DEFAULT_LAKEHOUSE_LOCK = threading.Lock()


def get_default_lakehouse(
    storage_path: str = ".local/observation_lakehouse",
) -> ObservationLakehouse:
    global _DEFAULT_LAKEHOUSE
    if _DEFAULT_LAKEHOUSE is None:
        with _DEFAULT_LAKEHOUSE_LOCK:
            if _DEFAULT_LAKEHOUSE is None:
                _DEFAULT_LAKEHOUSE = ObservationLakehouse(storage_path=storage_path)
    return _DEFAULT_LAKEHOUSE


def _read_file(path: Path) -> list[ObservationEvent]:
    out: list[ObservationEvent] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("Skipping malformed observation row in %s", path)
                    continue
                out.append(ObservationEvent.from_dict(data))
    except FileNotFoundError:
        return []
    return out


def _parse_iso(value: str) -> datetime:
    cleaned = value.replace("Z", "+00:00") if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return datetime.now(UTC)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, set):
        return sorted(value)
    if hasattr(value, "value") and isinstance(value.value, (str, int, float, bool)):
        return value.value
    return str(value)


def asdict_event(event: ObservationEvent) -> dict[str, Any]:
    """Serialize an event for HTTP responses."""
    data = asdict(event)
    data["id"] = str(data["id"])
    data["recorded_at"] = event.recorded_at.astimezone(UTC).isoformat()
    return data
