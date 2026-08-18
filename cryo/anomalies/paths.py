"""Pure overlap detection for Iceberg table warehouse locations.

Two catalog names that share a warehouse (exact prefix, or one nested under the
other) are one physical table. DROP / expire / orphan-remove on either name
deletes the other's files. See ``docs/incidents/rca-cdp-catalog-evolving-shared-s3-drop.md``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID

OverlapKind = Literal["exact", "nested"]

_S3_SCHEMES = {"s3", "s3a", "s3n"}


@dataclass(frozen=True)
class LocationRow:
    """One catalog table plus its warehouse URI, ready for overlap grouping."""

    source_id: UUID
    source_name: str
    source_type: str
    schema_name: str
    table_name: str
    location: str
    table_id: UUID | None = None


@dataclass(frozen=True)
class OverlapMember:
    source_id: UUID
    source_name: str
    source_type: str
    schema_name: str
    table_name: str
    location: str
    table_id: UUID | None
    qualified_name: str
    normalized_path: str


@dataclass(frozen=True)
class OverlapGroup:
    shared_path: str
    kind: OverlapKind
    tables: tuple[OverlapMember, ...]

    @property
    def table_count(self) -> int:
        return len(self.tables)


@dataclass(frozen=True)
class OverlapDetection:
    groups: tuple[OverlapGroup, ...]
    usable_count: int
    skipped_count: int


def normalize_warehouse_path(location: str | None) -> str | None:
    """Canonical ``s3://bucket/key`` form, or None if not an S3 table root.

    ``s3`` / ``s3a`` / ``s3n`` are the same object store; trailing slashes and
    duplicate path separators are stripped. Bucket names are lowercased (S3
    DNS); the key is left case-sensitive.
    """
    if not location or not location.strip():
        return None
    parsed = urlparse(location.strip())
    if parsed.scheme.lower() not in _S3_SCHEMES:
        return None
    bucket = parsed.netloc.strip().lower()
    if not bucket:
        return None
    key = "/".join(part for part in parsed.path.split("/") if part)
    return f"s3://{bucket}/{key}" if key else f"s3://{bucket}"


def is_path_prefix(parent: str, child: str) -> bool:
    """True when ``parent`` is ``child`` or a path-boundary prefix of it.

    ``s3://b/orders`` matches ``s3://b/orders/nested``, not ``s3://b/orders_v2``.
    """
    if parent == child:
        return True
    return child.startswith(f"{parent}/")


def detect_shared_s3_groups(rows: list[LocationRow]) -> OverlapDetection:
    """Group tables whose warehouse paths are equal or nested.

    Isolated tables (unique path, not a prefix of another) are omitted.
    """
    indexed: list[tuple[LocationRow, str]] = []
    skipped = 0
    for row in rows:
        normalized = normalize_warehouse_path(row.location)
        if normalized is None:
            skipped += 1
            continue
        indexed.append((row, normalized))

    if not indexed:
        return OverlapDetection(groups=(), usable_count=0, skipped_count=skipped)

    parent = list(range(len(indexed)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(left: int, right: int) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    by_path: dict[str, list[int]] = defaultdict(list)
    for index, (_, path) in enumerate(indexed):
        by_path[path].append(index)

    for indices in by_path.values():
        first = indices[0]
        for other in indices[1:]:
            union(first, other)

    # Walk unique paths in lexicographic order. A nested path always sorts after
    # its parent, so a stack of ancestors finds prefix relationships in O(n).
    ancestors: list[str] = []
    for path in sorted(by_path):
        while ancestors and not is_path_prefix(ancestors[-1], path):
            ancestors.pop()
        if ancestors:
            union(by_path[path][0], by_path[ancestors[-1]][0])
        ancestors.append(path)

    components: dict[int, list[int]] = defaultdict(list)
    for index in range(len(indexed)):
        components[find(index)].append(index)

    groups: list[OverlapGroup] = []
    for members in components.values():
        if len(members) < 2:
            continue
        groups.append(_build_group([indexed[i] for i in members]))

    groups.sort(key=lambda group: (-group.table_count, group.shared_path))
    return OverlapDetection(
        groups=tuple(groups),
        usable_count=len(indexed),
        skipped_count=skipped,
    )


def _build_group(members: list[tuple[LocationRow, str]]) -> OverlapGroup:
    paths = {path for _, path in members}
    shared = min(paths, key=lambda path: (path.count("/"), len(path), path))
    kind: OverlapKind = "exact" if len(paths) == 1 else "nested"
    tables = tuple(
        sorted(
            (_to_member(row, path) for row, path in members),
            key=lambda member: (member.qualified_name, member.normalized_path),
        )
    )
    return OverlapGroup(shared_path=shared, kind=kind, tables=tables)


def _to_member(row: LocationRow, normalized_path: str) -> OverlapMember:
    qualified = f"{row.source_name}.{row.schema_name}.{row.table_name}"
    return OverlapMember(
        source_id=row.source_id,
        source_name=row.source_name,
        source_type=row.source_type,
        schema_name=row.schema_name,
        table_name=row.table_name,
        location=row.location,
        table_id=row.table_id,
        qualified_name=qualified,
        normalized_path=normalized_path,
    )
