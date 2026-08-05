from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass
from typing import Literal

PartitionTransform = Literal["unpartitioned", "identity", "day", "month", "year"]
SizeClass = Literal["tiny", "standard", "large", "outlier"]


@dataclass(frozen=True)
class CustomerFleetSpec:
    table_count: int = 12_938
    tenant_count: int = 12
    source_count: int = 3
    seed: int = 20260804
    outlier_partition_count: int = 397_000

    def __post_init__(self) -> None:
        if self.table_count <= 0:
            raise ValueError("table_count must be positive")
        if self.tenant_count <= 0 or self.source_count <= 0:
            raise ValueError("tenant_count and source_count must be positive")
        if self.outlier_partition_count <= 0:
            raise ValueError("outlier_partition_count must be positive")


@dataclass(frozen=True)
class SyntheticTable:
    table_ref: str
    tenant: str
    source: str
    size_class: SizeClass
    partition_transform: PartitionTransform
    total_size_bytes: int
    partition_count: int
    total_files: int
    small_files: int
    delete_files: int
    snapshot_count: int
    query_count_30d: int


@dataclass(frozen=True)
class FleetManifest:
    schema_version: str
    spec: CustomerFleetSpec
    tables: tuple[SyntheticTable, ...]
    digest: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "spec": asdict(self.spec),
            "summary": summarize_fleet(self.tables),
            "digest": self.digest,
            "tables": [asdict(table) for table in self.tables],
        }


def generate_customer_fleet(spec: CustomerFleetSpec | None = None) -> FleetManifest:
    """Generate a deterministic, heavy-tailed fleet shaped like Cryo's known deployments."""
    spec = spec or CustomerFleetSpec()
    rng = random.Random(spec.seed)
    tables = tuple(_generate_table(rng, spec, index) for index in range(spec.table_count))
    canonical = json.dumps(
        [asdict(table) for table in tables],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return FleetManifest(
        schema_version="cryobench.fleet/v1",
        spec=spec,
        tables=tables,
        digest=f"sha256:{hashlib.sha256(canonical).hexdigest()}",
    )


def summarize_fleet(tables: tuple[SyntheticTable, ...]) -> dict[str, object]:
    if not tables:
        raise ValueError("tables must not be empty")
    partitions = sorted(table.partition_count for table in tables)
    sizes = sorted(table.total_size_bytes for table in tables)
    return {
        "table_count": len(tables),
        "total_size_bytes": sum(sizes),
        "total_files": sum(table.total_files for table in tables),
        "total_small_files": sum(table.small_files for table in tables),
        "partition_count": {
            "p50": _percentile(partitions, 0.50),
            "p95": _percentile(partitions, 0.95),
            "p99": _percentile(partitions, 0.99),
            "max": partitions[-1],
        },
        "table_size_bytes": {
            "p50": _percentile(sizes, 0.50),
            "p95": _percentile(sizes, 0.95),
            "p99": _percentile(sizes, 0.99),
            "max": sizes[-1],
        },
        "size_classes": {
            size_class: sum(table.size_class == size_class for table in tables)
            for size_class in ("tiny", "standard", "large", "outlier")
        },
    }


def _generate_table(
    rng: random.Random,
    spec: CustomerFleetSpec,
    index: int,
) -> SyntheticTable:
    if index == spec.table_count - 1:
        size_class: SizeClass = "outlier"
        partition_count = spec.outlier_partition_count
        total_size_bytes = 80 * 1024**4
    else:
        draw = rng.random()
        if draw < 0.50:
            size_class = "tiny"
            total_size_bytes = int(rng.lognormvariate(math.log(2 * 1024**3), 0.8))
            partition_count = rng.randint(0, 31)
        elif draw < 0.90:
            size_class = "standard"
            total_size_bytes = int(rng.lognormvariate(math.log(80 * 1024**3), 1.0))
            partition_count = rng.randint(1, 2_000)
        else:
            size_class = "large"
            total_size_bytes = int(rng.lognormvariate(math.log(3 * 1024**4), 1.1))
            partition_count = rng.randint(500, 50_000)

    transform = _partition_transform(rng, partition_count)
    target_file_bytes = 512 * 1024**2
    healthy_files = max(1, math.ceil(total_size_bytes / target_file_bytes))
    fragmentation = rng.randint(1, 12)
    total_files = max(1, healthy_files * fragmentation)
    small_files = min(total_files, int(total_files * rng.uniform(0.05, 0.95)))

    return SyntheticTable(
        table_ref=f"bench_{index % spec.source_count}.domain_{index % 97}.table_{index:05d}",
        tenant=f"tenant-{index % spec.tenant_count:02d}",
        source=f"source-{index % spec.source_count:02d}",
        size_class=size_class,
        partition_transform=transform,
        total_size_bytes=total_size_bytes,
        partition_count=partition_count,
        total_files=total_files,
        small_files=small_files,
        delete_files=int(total_files * rng.uniform(0.0, 0.15)),
        snapshot_count=rng.randint(1, 2_000),
        query_count_30d=int(rng.lognormvariate(math.log(100), 1.7)),
    )


def _partition_transform(rng: random.Random, partition_count: int) -> PartitionTransform:
    if partition_count == 0:
        return "unpartitioned"
    return rng.choices(
        ("identity", "day", "month", "year"),
        weights=(20, 50, 20, 10),
        k=1,
    )[0]


def _percentile(values: list[int], fraction: float) -> int:
    index = math.ceil(fraction * len(values)) - 1
    return values[max(0, min(index, len(values) - 1))]
