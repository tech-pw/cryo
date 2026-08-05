from __future__ import annotations

from pathlib import Path

from cryo.devtools.baseline import build_snapshot
from cryo.domain.enums import EngineType, OperationType

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_baseline_snapshot_is_deterministic_and_complete() -> None:
    first = build_snapshot(REPO_ROOT)
    second = build_snapshot(REPO_ROOT)

    assert first == second
    capabilities = first["capabilities"]
    assert isinstance(capabilities, dict)
    assert capabilities["matrix_cells"] == len(EngineType) * len(OperationType)
    assert capabilities["matrix_cells"] == capabilities["expected_matrix_cells"]


def test_baseline_detects_the_shipped_frontend_stack() -> None:
    snapshot = build_snapshot(REPO_ROOT)
    frontend = snapshot["frontend_stack"]

    assert isinstance(frontend, dict)
    assert frontend["antd_import_files"] == []
    assert frontend["antd_dependency_present"] is False
    assert isinstance(frontend["radix_packages"], int)
    assert frontend["radix_packages"] > 0


def test_baseline_records_one_linear_migration_head() -> None:
    snapshot = build_snapshot(REPO_ROOT)
    migrations = snapshot["migrations"]

    assert isinstance(migrations, dict)
    assert migrations["files"] == migrations["revisions"]
    assert isinstance(migrations["heads"], list)
    assert len(migrations["heads"]) == 1


def test_baseline_splits_frontend_hotspots_by_owned_surface() -> None:
    snapshot = build_snapshot(REPO_ROOT)
    packages = snapshot["packages"]

    assert isinstance(packages, list)
    package_names = {package["package"] for package in packages}
    assert "web/src/pages" in package_names
    assert "web/src/components" in package_names
    assert "web/src" not in package_names
