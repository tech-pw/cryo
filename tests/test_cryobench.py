from __future__ import annotations

import json

from cryo.benchmarks.cli import main
from cryo.benchmarks.fault_matrix import (
    CONTROL_PLANE_FAULT_MATRIX,
    fault_matrix_report,
    validate_fault_matrix,
)
from cryo.benchmarks.fleet import CustomerFleetSpec, generate_customer_fleet


def test_customer_fleet_is_deterministic_and_contains_declared_outlier() -> None:
    spec = CustomerFleetSpec(
        table_count=100,
        tenant_count=5,
        source_count=2,
        seed=42,
        outlier_partition_count=397_000,
    )

    first = generate_customer_fleet(spec)
    second = generate_customer_fleet(spec)

    assert first == second
    assert first.digest.startswith("sha256:")
    assert max(table.partition_count for table in first.tables) == 397_000
    assert {table.tenant for table in first.tables} == {f"tenant-{index:02d}" for index in range(5)}
    assert {table.source for table in first.tables} == {"source-00", "source-01"}


def test_customer_fleet_changes_when_seed_changes() -> None:
    first = generate_customer_fleet(CustomerFleetSpec(table_count=10, seed=1))
    second = generate_customer_fleet(CustomerFleetSpec(table_count=10, seed=2))

    assert first.digest != second.digest
    assert first.tables != second.tables


def test_fault_matrix_never_claims_coverage_without_evidence() -> None:
    validate_fault_matrix()

    report = fault_matrix_report()
    assert report["schema_version"] == "cryobench.fault-matrix/v1"
    assert len(CONTROL_PLANE_FAULT_MATRIX) == 9
    assert report["summary"] == {"covered": 6, "partial": 3, "open": 0}


def test_cli_writes_versioned_reproducible_manifest(tmp_path) -> None:
    output = tmp_path / "cryobench.json"

    main(
        [
            "--output",
            str(output),
            "--tables",
            "25",
            "--tenants",
            "3",
            "--sources",
            "2",
            "--seed",
            "7",
        ]
    )

    document = json.loads(output.read_text())
    assert document["schema_version"] == "cryobench/v1"
    assert document["fleet"]["summary"]["table_count"] == 25
    assert document["fleet"]["spec"]["seed"] == 7
    assert document["fault_matrix"]["summary"] == {"covered": 6, "partial": 3, "open": 0}
