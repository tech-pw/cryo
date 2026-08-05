"""Protocol conformance between cryo-engine-go and the Python runner."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from cryo.engines.native.runner import execute_request, split_stdout_lines

REPO_ROOT = Path(__file__).resolve().parents[1]
GO_BINARY = REPO_ROOT / "engine-go" / "bin" / "cryo-engine-go"


@pytest.mark.skipif(not GO_BINARY.is_file(), reason="cryo-engine-go binary not built")
def test_go_engine_invalid_json_returns_parse_error() -> None:
    proc = subprocess.run(
        [str(GO_BINARY), "execute"],
        input="{not-json",
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 1
    _, response_lines = split_stdout_lines(proc.stdout)
    assert response_lines
    payload = json.loads(response_lines[-1])
    assert payload["ok"] is False
    assert "failed to parse request" in (payload.get("error") or "")


@pytest.mark.skipif(not GO_BINARY.is_file(), reason="cryo-engine-go binary not built")
def test_go_engine_empty_plan_ok_shape() -> None:
    result = execute_request(
        {
            "table_ref": "demo.orders",
            "catalog_uri": "http://localhost:8181",
            "warehouse": "s3://warehouse/",
            "mode": "execute",
            "operations": [],
        },
        config={"binary_path": str(GO_BINARY), "engine_impl": "go"},
    )
    assert result.ok
    assert result.table_ref == "demo.orders"
    assert result.results == ()


@pytest.mark.skipif(not GO_BINARY.is_file(), reason="cryo-engine-go binary not built")
def test_go_engine_emits_progress_prefix() -> None:
    request = {
        "table_ref": "demo.orders",
        "catalog_uri": "http://127.0.0.1:1",
        "warehouse": "s3://warehouse/",
        "mode": "execute",
        "operations": [
            {"type": "expire_snapshots", "params": {"retention_days": 7, "retain_last": 1}}
        ],
    }
    proc = subprocess.run(
        [str(GO_BINARY), "execute"],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        timeout=30,
    )
    progress_lines, response_lines = split_stdout_lines(proc.stdout)
    assert response_lines
    assert any('"event":"started"' in line for line in progress_lines)


@pytest.mark.skipif(
    os.getenv("CRYO_RUN_GO_PROTOCOL_E2E") != "1", reason="set CRYO_RUN_GO_PROTOCOL_E2E=1"
)
def test_go_engine_unknown_subcommand_exit_2() -> None:
    if not GO_BINARY.is_file():
        pytest.skip("cryo-engine-go binary not built")
    proc = subprocess.run([str(GO_BINARY)], capture_output=True, text=True, timeout=5)
    assert proc.returncode == 2
