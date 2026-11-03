"""The Go scan subprocess stays inside the container memory limit (#378)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cryo.config import Settings
from cryo.scanner.go_scan import cgroup_memory_limit_bytes, run_scan_stats, scan_engine_gomemlimit


def test_cgroup_limit_ignores_unlimited_and_missing(tmp_path: Path) -> None:
    missing = tmp_path / "absent"
    unlimited = tmp_path / "max"
    unlimited.write_text("max\n")
    sentinel = tmp_path / "v1"
    sentinel.write_text(str(1 << 62))
    assert cgroup_memory_limit_bytes((str(missing), str(unlimited), str(sentinel))) is None


def test_cgroup_limit_reads_the_first_real_value(tmp_path: Path) -> None:
    first = tmp_path / "memory.max"
    first.write_text("4294967296\n")
    assert cgroup_memory_limit_bytes((str(first),)) == 4 * 1024**3


def test_explicit_memlimit_wins_over_the_cgroup(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "memory.max"
    path.write_text("4294967296\n")
    monkeypatch.setattr("cryo.scanner.go_scan.cgroup_memory_limit_bytes", lambda: 4 * 1024**3)
    settings = Settings(scan_engine_memlimit="2400MiB", scan_engine_memlimit_ratio=0.75)
    assert scan_engine_gomemlimit(settings) == "2400MiB"


def test_ratio_zero_disables_the_cgroup_limit(monkeypatch) -> None:
    monkeypatch.setattr("cryo.scanner.go_scan.cgroup_memory_limit_bytes", lambda: 4 * 1024**3)
    assert scan_engine_gomemlimit(Settings(scan_engine_memlimit_ratio=0)) is None


def test_ratio_uses_three_quarters_of_the_cgroup(monkeypatch) -> None:
    monkeypatch.setattr("cryo.scanner.go_scan.cgroup_memory_limit_bytes", lambda: 4 * 1024**3)
    assert scan_engine_gomemlimit(Settings()) == str(int(4 * 1024**3 * 0.75))


def test_run_scan_stats_sets_gomemlimit_and_names_a_killed_table(monkeypatch, caplog) -> None:
    import subprocess

    monkeypatch.setattr("cryo.scanner.go_scan.scan_engine_gomemlimit", lambda settings: "1000")
    monkeypatch.setattr(
        "cryo.scanner.go_scan._resolve_scan_binary", lambda settings: "/bin/cryo-engine-go"
    )
    seen: dict[str, object] = {}

    class Proc:
        returncode = -9
        stdout = b""
        stderr = b"killed"

    def fake_run(cmd, input, capture_output, check, env):  # noqa: A002
        seen["env"] = env
        return Proc()

    monkeypatch.setattr(subprocess, "run", fake_run)

    with caplog.at_level("WARNING"), pytest.raises(RuntimeError, match="db.orders"):
        run_scan_stats({"table_ref": "db.orders"}, settings=Settings())

    assert seen["env"]["GOMEMLIMIT"] == "1000"  # type: ignore[index]
    assert "db.orders" in caplog.text


def test_run_scan_stats_sends_the_request_json(monkeypatch) -> None:
    monkeypatch.setattr("cryo.scanner.go_scan.scan_engine_gomemlimit", lambda settings: None)
    monkeypatch.setattr(
        "cryo.scanner.go_scan._resolve_scan_binary", lambda settings: "/bin/cryo-engine-go"
    )
    seen: dict[str, object] = {}

    class Proc:
        returncode = 0
        stdout = b'{"ok": true, "total_files": 1}'
        stderr = b""

    def fake_run(cmd, input, capture_output, check, env):  # noqa: A002
        seen["input"] = input
        seen["env"] = env
        return Proc()

    import subprocess

    monkeypatch.setattr(subprocess, "run", fake_run)
    payload = run_scan_stats({"table_ref": "db.orders"}, settings=Settings())
    assert payload["ok"] is True
    assert json.loads(seen["input"]) == {"table_ref": "db.orders"}  # type: ignore[arg-type]
    assert "GOMEMLIMIT" not in seen["env"]  # type: ignore[operator]
