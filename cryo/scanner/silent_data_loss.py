"""Silent-data-loss detector for Iceberg tables (arXiv:2604.20081).

The paper formalizes two complementary failure modes for an Iceberg-style table format:

* **Orphans** -- data files that exist on the storage layer but are not referenced by any
  committed manifest list. In production these waste storage and inflate the S3 bill; in
  pathological cases they indicate that a writer failed mid-commit and left half-uploaded
  data.
* **Missing files** -- entries in committed manifests that no longer exist on the storage
  layer. These are the catastrophic case: a SELECT that walks the manifest list will skip
  rows it believes it has, *silently*. That's the "silent data loss" the paper warns about.

This module provides a single :func:`detect_silent_data_loss` function that cross-references
a list of "files actually on disk" against a list of "files referenced by committed
manifests" and returns an :class:`OrphanReport`. Both inputs are simple lists of strings
(paths) plus an optional ``size_bytes`` annotation -- the actual filesystem/S3 walk lives
in the caller's :func:`list_storage_files` implementation, which keeps this module testable
without a real catalog.

For local dev the companion :func:`list_local_storage_files` walks the
``.local/tables/<table_uuid>/data/`` directory; in production the same shape applies to a
``boto3`` S3 list-objects-v2 paginator.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


@dataclass
class StorageFile:
    """A file observed on the storage layer (S3 / local FS)."""

    path: str
    size_bytes: int = 0


@dataclass
class CommittedManifestEntry:
    """A data-file entry as recorded in a committed manifest list."""

    path: str
    size_bytes: int = 0
    snapshot_id: int | None = None


@dataclass
class OrphanReport:
    """Outcome of one silent-data-loss detection pass."""

    table_id: str
    snapshot_id: int | None
    orphan_data_files: list[str] = field(default_factory=list)
    orphan_size_bytes: int = 0
    missing_files: list[str] = field(default_factory=list)
    detected_at: str = ""
    root_cause: str = "unknown"

    def is_clean(self) -> bool:
        """Return True if neither orphans nor missing files were observed."""
        return not self.orphan_data_files and not self.missing_files


class StorageLister(Protocol):
    """Pluggable storage-layer listing.

    Production swaps this for an S3 (or GCS / Azure) lister; local dev uses
    :func:`list_local_storage_files`.
    """

    def list_files(self, table_id: str) -> list[StorageFile]: ...


class ManifestSource(Protocol):
    """Pluggable committed-manifest source.

    In production this is the Iceberg catalog's current-snapshot manifest walker; local dev
    accepts a fixed list of paths to simulate "what the table is committed to".
    """

    def list_entries(self, table_id: str) -> list[CommittedManifestEntry]: ...


def detect_silent_data_loss(
    table_id: str,
    storage_files: Iterable[StorageFile],
    committed_entries: Iterable[CommittedManifestEntry],
    *,
    snapshot_id: int | None = None,
) -> OrphanReport:
    """Cross-reference storage vs committed manifests and return an :class:`OrphanReport`.

    The detection is purely set-based: any file present on storage but absent from
    committed entries is an *orphan*; any committed path absent from storage is *missing*.
    Both sets are returned, plus a coarse ``root_cause`` heuristic the API can surface to
    operators.
    """
    storage_set: dict[str, int] = {}
    for file in storage_files:
        # Normalize trailing slashes so a path like ``s3://bucket/data/foo.parquet`` and
        # ``s3://bucket/data/foo.parquet/`` compare equal -- some storage backends append
        # an artifact separator that should not affect our diff.
        normalized = file.path.rstrip("/")
        if normalized:
            storage_set[normalized] = int(file.size_bytes or 0)

    committed: dict[str, int] = {}
    for entry in committed_entries:
        normalized = entry.path.rstrip("/")
        if normalized:
            committed[normalized] = int(entry.size_bytes or 0)

    orphan_paths = sorted(set(storage_set) - set(committed))
    missing_paths = sorted(set(committed) - set(storage_set))

    orphan_size = sum(storage_set[path] for path in orphan_paths)

    root_cause = _infer_root_cause(orphan_paths, missing_paths)

    return OrphanReport(
        table_id=table_id,
        snapshot_id=snapshot_id,
        orphan_data_files=orphan_paths,
        orphan_size_bytes=orphan_size,
        missing_files=missing_paths,
        detected_at=datetime.now(UTC).isoformat(),
        root_cause=root_cause,
    )


def _contained_dir(base: Path, table_key: str, data_subdir: str) -> Path | None:
    root = base.expanduser().resolve()
    candidate = root / table_key / data_subdir
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    if not resolved.is_relative_to(root):
        return None
    return resolved


def list_local_storage_files(
    table_id: str,
    *,
    root: str | os.PathLike[str] | None = None,
    data_subdir: str = "data",
) -> list[StorageFile]:
    """Walk ``<root>/<table_uuid>/<data_subdir>/`` and return any ``*.parquet`` / ``*.avro``
    files.

    The Iceberg data-directory layout under S3 mirrors the on-disk layout the local scanner
    uses, so this works as a faithful stand-in for the ``list-objects-v2`` walk a production
    :class:`StorageLister` would perform.
    """
    from cryo.config import get_settings

    settings = get_settings()
    base = Path(root or settings.silent_loss_local_root or ".local/tables")
    table_key = str(table_id).replace("-", "")
    if not table_key.isalnum() or data_subdir not in {"data", "metadata"}:
        return []
    table_dir = _contained_dir(base, table_key, data_subdir)
    if table_dir is None or not table_dir.exists():
        return []
    files: list[StorageFile] = []
    for entry in sorted(table_dir.rglob("*")):
        if not entry.is_file():
            continue
        if entry.suffix.lower() not in {".parquet", ".avro", ".orc"}:
            continue
        try:
            size = entry.stat().st_size
        except OSError:
            size = 0
        files.append(StorageFile(path=str(entry), size_bytes=size))
    return files


def commit_orphan_event(
    session: object,
    report: OrphanReport,
) -> object:
    """Persist an :class:`OrphanReport` into the ``silent_data_loss_events`` table.

    Returns the persisted :class:`SilentDataLossEvent` row. The session is intentionally
    left to the caller -- we don't commit here so the caller's transaction boundary
    remains the source of truth.
    """
    from cryo.db.models import SilentDataLossEvent

    if report.is_clean():
        return None
    row = SilentDataLossEvent(
        table_id=_coerce_uuid(report.table_id),
        detected_at=datetime.now(UTC),
        orphan_data_files=list(report.orphan_data_files),
        orphan_size_bytes=int(report.orphan_size_bytes),
        missing_files=list(report.missing_files),
        root_cause=str(report.root_cause or "unknown"),
    )
    session.add(row)  # type: ignore[attr-defined]
    return row


def _infer_root_cause(orphans: list[str], missing: list[str]) -> str:
    """Best-effort root-cause string for the report.

    The paper's classification (writer crash mid-commit vs. lifecycle policy vs.
    versioning) requires more context than we can synthesize here without an S3 inventory
    trail, so this is intentionally coarse -- it distinguishes the two failure modes and
    flags "clean" tables explicitly.
    """
    if orphans and missing:
        return "concurrent_writer_drift"
    if missing:
        return "missing_committed_files"
    if orphans:
        return "orphan_files_on_storage"
    return "clean"


def _coerce_uuid(value: object) -> object:
    """Best-effort coercion of a string/uuid to :class:`uuid.UUID`."""
    import uuid as _uuid

    if isinstance(value, _uuid.UUID):
        return value
    if isinstance(value, str):
        try:
            return _uuid.UUID(value)
        except ValueError:
            return value
    return value


__all__ = [
    "CommittedManifestEntry",
    "OrphanReport",
    "StorageFile",
    "StorageLister",
    "ManifestSource",
    "commit_orphan_event",
    "detect_silent_data_loss",
    "list_local_storage_files",
]
