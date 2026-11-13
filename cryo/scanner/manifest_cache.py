"""Immutable Iceberg manifest summary cache on local disk (#532).

Iceberg manifest files are immutable once written: any commit writes new manifest files under
new paths. We cache parsed per-manifest entry summaries keyed by a SHA-256 of the manifest
path (plus schema version marker) in a local directory with LRU-by-mtime size eviction.

Both the Python scanner and the Go ``scan-stats`` engine read and write the exact same JSON
file layout so cached manifests are shared across engines on a scanner pod.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CACHE_SCHEMA_VERSION = 1
_EVICT_LOCK = threading.Lock()


@dataclass(frozen=True)
class CachedManifestEntry:
    file_path: str
    file_size_in_bytes: int | None
    record_count: int
    content: str
    file_format: str
    spec_id: int | None
    partition_values: dict[str, Any]
    null_value_counts: dict[int, int]
    value_counts: dict[int, int]
    lower_bounds: dict[int, bytes]
    upper_bounds: dict[int, bytes]
    snapshot_id: int | None


class _CachedDataFileProxy:
    """Lightweight stand-in matching the PyIceberg DataFile attributes read by analyze.py."""

    __slots__ = (
        "_partition_by_name",
        "content",
        "file_format",
        "file_path",
        "file_size_in_bytes",
        "lower_bounds",
        "null_value_counts",
        "partition",
        "record_count",
        "spec_id",
        "upper_bounds",
        "value_counts",
    )

    def __init__(self, item: CachedManifestEntry) -> None:
        self.file_path = item.file_path
        self.file_size_in_bytes = item.file_size_in_bytes
        self.record_count = item.record_count
        self.content = item.content
        self.file_format = item.file_format
        self.spec_id = item.spec_id
        self.partition = dict(item.partition_values)
        self._partition_by_name = self.partition
        self.null_value_counts = item.null_value_counts
        self.value_counts = item.value_counts
        self.lower_bounds = item.lower_bounds
        self.upper_bounds = item.upper_bounds


class CachedEntryProxy:
    """Stand-in matching the PyIceberg ManifestEntry attributes read by analyze.py."""

    __slots__ = ("data_file", "snapshot_id", "status")

    def __init__(self, item: CachedManifestEntry) -> None:
        self.data_file = _CachedDataFileProxy(item)
        self.snapshot_id = item.snapshot_id
        self.status = 1  # EXISTING / ADDED (deleted entries are already filtered before caching)


def manifest_cache_key(manifest_path: str) -> str:
    raw = f"v{CACHE_SCHEMA_VERSION}:{manifest_path}".encode()
    return hashlib.sha256(raw).hexdigest()


def _entry_file_path(cache_dir: str, manifest_path: str) -> Path:
    digest = manifest_cache_key(manifest_path)
    return Path(cache_dir) / digest[:2] / f"{digest}.json"


class ManifestSummaryCache:
    """Local-disk LRU cache of immutable Iceberg manifest entry summaries."""

    def __init__(self, cache_dir: str, *, max_bytes: int) -> None:
        self.cache_dir = cache_dir.strip()
        self.max_bytes = max_bytes

    @property
    def enabled(self) -> bool:
        return bool(self.cache_dir) and self.max_bytes > 0

    def get(self, manifest_path: str) -> list[CachedEntryProxy] | None:
        if not self.enabled or not manifest_path:
            return None
        path = _entry_file_path(self.cache_dir, manifest_path)
        try:
            raw = path.read_bytes()
        except OSError:
            return None
        try:
            os.utime(path, None)
        except OSError:
            pass
        try:
            doc = json.loads(raw.decode("utf-8"))
            if not isinstance(doc, dict) or doc.get("version") != CACHE_SCHEMA_VERSION:
                return None
            if doc.get("manifest_path") != manifest_path:
                return None
            items = doc.get("entries")
            if not isinstance(items, list):
                return None
            return [CachedEntryProxy(_decode_entry(item)) for item in items]
        except Exception:  # noqa: BLE001 - corrupt cache file is treated as a miss
            return None

    def put(self, manifest_path: str, entries: list[CachedManifestEntry]) -> None:
        if not self.enabled or not manifest_path:
            return
        target = _entry_file_path(self.cache_dir, manifest_path)
        payload = json.dumps(
            {
                "version": CACHE_SCHEMA_VERSION,
                "manifest_path": manifest_path,
                "entries": [_encode_entry(item) for item in entries],
            },
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > self.max_bytes:
            return
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".tmp-")
            try:
                os.write(fd, payload)
            finally:
                os.close(fd)
            os.replace(tmp_name, target)
            self._evict_if_needed()
        except OSError:
            return

    def _evict_if_needed(self) -> None:
        if not self.enabled:
            return
        with _EVICT_LOCK:
            root = Path(self.cache_dir)
            if not root.exists():
                return
            files: list[tuple[float, int, Path]] = []
            total = 0
            for sub in root.iterdir():
                if not sub.is_dir():
                    continue
                for candidate in sub.iterdir():
                    if not candidate.name.endswith(".json"):
                        continue
                    try:
                        st = candidate.stat()
                    except OSError:
                        continue
                    total += st.st_size
                    files.append((st.st_mtime, st.st_size, candidate))
            if total <= self.max_bytes:
                return
            # Evict oldest-accessed/modified files first until under max_bytes.
            files.sort(key=lambda item: item[0])
            for _mtime, size, victim in files:
                if total <= self.max_bytes:
                    break
                try:
                    victim.unlink()
                    total -= size
                except OSError:
                    continue


def _encode_entry(entry: CachedManifestEntry) -> dict[str, Any]:
    return {
        "file_path": entry.file_path,
        "file_size_in_bytes": entry.file_size_in_bytes,
        "record_count": entry.record_count,
        "content": entry.content,
        "file_format": entry.file_format,
        "spec_id": entry.spec_id,
        "partition_values": entry.partition_values,
        "null_value_counts": {str(k): v for k, v in entry.null_value_counts.items()},
        "value_counts": {str(k): v for k, v in entry.value_counts.items()},
        "lower_bounds": {
            str(k): base64.b64encode(v).decode("ascii") for k, v in entry.lower_bounds.items()
        },
        "upper_bounds": {
            str(k): base64.b64encode(v).decode("ascii") for k, v in entry.upper_bounds.items()
        },
        "snapshot_id": entry.snapshot_id,
    }


def _decode_entry(raw: Mapping_or_dict) -> CachedManifestEntry:
    nulls = {int(k): int(v) for k, v in (raw.get("null_value_counts") or {}).items()}
    vals = {int(k): int(v) for k, v in (raw.get("value_counts") or {}).items()}
    lows = {
        int(k): base64.b64decode(v)
        for k, v in (raw.get("lower_bounds") or {}).items()
        if isinstance(v, str)
    }
    highs = {
        int(k): base64.b64decode(v)
        for k, v in (raw.get("upper_bounds") or {}).items()
        if isinstance(v, str)
    }
    size = raw.get("file_size_in_bytes")
    spec_id = raw.get("spec_id")
    snap_id = raw.get("snapshot_id")
    return CachedManifestEntry(
        file_path=str(raw.get("file_path") or ""),
        file_size_in_bytes=int(size) if size is not None else None,
        record_count=int(raw.get("record_count") or 0),
        content=str(raw.get("content") or ""),
        file_format=str(raw.get("file_format") or ""),
        spec_id=int(spec_id) if spec_id is not None else None,
        partition_values=dict(raw.get("partition_values") or {}),
        null_value_counts=nulls,
        value_counts=vals,
        lower_bounds=lows,
        upper_bounds=highs,
        snapshot_id=int(snap_id) if snap_id is not None else None,
    )


Mapping_or_dict = dict[str, Any]
