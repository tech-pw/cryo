"""Tests for the one-shot scan-chunk Job entrypoint (cryo/scanner/job_runner.py)."""

from __future__ import annotations

import base64
import json
from unittest.mock import patch

from cryo.scanner.job_runner import main
from cryo.scanner.worker import ScanWorkResult


def _b64(payload: dict) -> str:
    return base64.b64encode(json.dumps(payload).encode()).decode()


def test_missing_payload_env_fails(monkeypatch, capsys) -> None:
    monkeypatch.delenv("CRYO_PAYLOAD_B64", raising=False)
    assert main([]) == 1
    out = json.loads(capsys.readouterr().out.strip())
    assert out["ok"] is False


def test_invalid_payload_fails(monkeypatch, capsys) -> None:
    monkeypatch.setenv("CRYO_PAYLOAD_B64", "not-valid-base64-json")
    assert main([]) == 1
    out = json.loads(capsys.readouterr().out.strip())
    assert out["ok"] is False


def test_succeeded_chunk_returns_zero(monkeypatch, capsys) -> None:
    monkeypatch.setenv("CRYO_PAYLOAD_B64", _b64({"run_id": "r1", "chunk_id": 0, "num_chunks": 1}))
    with patch(
        "cryo.scanner.job_runner.ScanWorker.process_next",
        return_value=ScanWorkResult(run_id="r1", status="succeeded"),
    ):
        assert main([]) == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out == {"ok": True, "run_id": "r1", "status": "succeeded"}


def test_failed_chunk_returns_nonzero(monkeypatch, capsys) -> None:
    monkeypatch.setenv("CRYO_PAYLOAD_B64", _b64({"run_id": "r1", "chunk_id": 0, "num_chunks": 1}))
    with patch(
        "cryo.scanner.job_runner.ScanWorker.process_next",
        return_value=ScanWorkResult(run_id="r1", status="failed"),
    ):
        assert main([]) == 1
    out = json.loads(capsys.readouterr().out.strip())
    assert out["ok"] is False
    assert out["status"] == "failed"


def test_worker_exception_is_caught(monkeypatch, capsys) -> None:
    monkeypatch.setenv("CRYO_PAYLOAD_B64", _b64({"run_id": "r1", "chunk_id": 0, "num_chunks": 1}))
    with patch(
        "cryo.scanner.job_runner.ScanWorker.process_next",
        side_effect=RuntimeError("boom"),
    ):
        assert main([]) == 1
    out = json.loads(capsys.readouterr().out.strip())
    assert out["ok"] is False
    assert "boom" in out["error"]
