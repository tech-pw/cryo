"""Match an S3 request-log key's Hive-style path fragment to a scanned partition.

Part of docs/PARTITION_ACCESS_VISUALIZATION_PLAN_2026-08.md -- inspired by Yelp's engineering
blog post on partition access visualizations (S3 access logs -> partition-vs-time scatter plot
-> storage-class/retention decisions). This module is the one genuinely new piece that plan
needed: everything else (S3 log ingestion, per-table prefix matching, a structured per-partition
value model) already existed in some form.

Pure / no I/O: callers pass in an already-matched table's data-prefix and its already-loaded
``TablePartition`` rows for the latest scan (see ``cryo.db.repositories.job_store.
_latest_partitions`` for the existing "latest scan for a table" query pattern this mirrors).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import unquote

# Iceberg/Hive partition directory segments look like "col=value" or, for the DAY/HOUR/etc.
# transforms, the raw transformed value (e.g. "dt=2026-08-19", "event_ts_hour=2026-08-19-14").
# Values are URL-encoded in the actual S3 key the way any path segment is (e.g. a "/" inside a
# value would itself be encoded, so splitting on raw "/" boundaries first is safe).


@dataclass(frozen=True)
class PartitionPathMatch:
    """Result of matching one S3 key's residual path against a table's known partitions.

    ``partition_path`` is always populated (the raw parsed fragment, "" for an unpartitioned
    table) even when ``matched`` is False -- an unmatched path is still worth recording (see
    the PartitionAccessDaily docstring): it means either the partition was scanned away since
    the log line was written, or no scan has captured it yet, both real and visible states
    rather than something to silently drop.
    """

    partition_path: str
    matched: bool


def parse_partition_segments(residual_path: str) -> dict[str, str]:
    """Extract ``{column: value}`` pairs from the portion of an S3 key after a table's data
    prefix, e.g. ``"dt=2026-08-19/00000-0-uuid.parquet"`` -> ``{"dt": "2026-08-19"}``.

    Only ``col=value``-shaped segments count as partition directories; the final segment is
    always the data file itself and is dropped whether or not it happens to contain an ``=``
    (Iceberg data file names don't, but this stays correct even if some writer's naming scheme
    ever did). A residual path with no ``=``-shaped segments at all (unpartitioned table, or a
    key that isn't actually under a partition directory) returns an empty dict, not an error.
    """
    segments = [seg for seg in residual_path.split("/") if seg]
    if not segments:
        return {}
    # Drop the trailing file-name segment -- it's never a partition directory.
    directory_segments = segments[:-1]
    values: dict[str, str] = {}
    for segment in directory_segments:
        if "=" not in segment:
            continue
        column, _, raw_value = segment.partition("=")
        values[unquote(column)] = unquote(raw_value)
    return values


def partition_path_from_segments(residual_path: str) -> str:
    """The raw Hive-style directory fragment (everything but the trailing file name), used as
    ``PartitionAccessDaily.partition_path`` regardless of whether it matches a known partition.
    """
    segments = [seg for seg in residual_path.split("/") if seg]
    if len(segments) <= 1:
        return ""
    return "/".join(segments[:-1]) + "/"


def match_partition_path(
    residual_path: str,
    known_partitions: Sequence[Mapping[str, object]],
) -> PartitionPathMatch:
    """Match one key's residual path (everything after the table's ``data/`` prefix) against
    the table's latest-scanned partitions.

    ``known_partitions`` is the same shape ``job_store._latest_partitions`` already returns:
    an iterable of mappings each carrying a ``"partition_values"`` dict (column -> value,
    already-decoded strings). A match requires every parsed segment to agree with the scanned
    partition's own values -- a subset match (e.g. only ``dt`` parsed but the table is
    partitioned on ``dt, hour``) is deliberately NOT treated as a match, since that would
    silently attribute an access to the wrong (coarser) partition than the one actually read.
    """
    partition_path = partition_path_from_segments(residual_path)
    parsed = parse_partition_segments(residual_path)
    if not parsed:
        return PartitionPathMatch(partition_path=partition_path, matched=False)

    for candidate in known_partitions:
        candidate_values = candidate.get("partition_values")
        if not isinstance(candidate_values, Mapping) or not candidate_values:
            continue
        # Exact key-set equality, not just "every parsed key agrees" -- a parsed dict that's
        # a strict subset of the candidate's own columns (e.g. only `dt` parsed but the table
        # is partitioned on `dt, hour`) must NOT match; see the subset-match warning above.
        if set(parsed) != set(candidate_values):
            continue
        if all(str(candidate_values.get(k)) == v for k, v in parsed.items()):
            return PartitionPathMatch(partition_path=partition_path, matched=True)

    return PartitionPathMatch(partition_path=partition_path, matched=False)
