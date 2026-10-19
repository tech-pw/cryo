from __future__ import annotations

import uuid

import pytest

from cryo.ai.tools.evidence import redact
from cryo.scanner import control


def test_sql_literal_redaction_does_not_backtrack() -> None:
    payload = "'" + ("\\" * 80)
    assert redact(payload) == payload
    assert redact("select 'secret' from t") == "select '[literal]' from t"


def test_token_url_is_still_redacted() -> None:
    value = "see https://example.com/cb?token=abc&x=1"
    assert "abc" not in redact(value)
    assert "token=[redacted]" in redact(value)


def test_health_check_does_not_return_exception_text(monkeypatch: pytest.MonkeyPatch) -> None:
    import redis

    from cryo.observability import health

    class BrokenSession:
        def __enter__(self) -> None:
            raise RuntimeError("postgresql://user:secret@db/cryo")

        def __exit__(self, *_args: object) -> bool:
            return False

    class BrokenRedis:
        def ping(self) -> None:
            raise RuntimeError("redis://:secret@redis:6379/0")

    monkeypatch.setattr(health, "SessionLocal", lambda: BrokenSession())
    monkeypatch.setattr(redis, "from_url", lambda *_args, **_kwargs: BrokenRedis())
    result = health.health_check()
    rendered = str(result)
    assert "secret" not in rendered
    assert result["checks"]["database"] == {"status": "error"}
    assert result["checks"]["redis"] == {"status": "error"}


def test_scan_queue_failure_hides_exception_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(control.scanner_repo, "initialize_scan_chunks", lambda *_a, **_k: None)

    class Queue:
        def enqueue_chunks(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("redis://:secret@redis:6379/0")

    result = control.enqueue_scan_run(
        object(),
        Queue(),  # type: ignore[arg-type]
        run_id=uuid.uuid4(),
        payload={},
        num_chunks=1,
    )
    assert "secret" not in result.message
    assert result.error == "queueing failed"
