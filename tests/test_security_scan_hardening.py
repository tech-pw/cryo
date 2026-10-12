"""Regression tests for the 2026-10-12 security-scan fixes (docs/security-scan-2026-10-12.md)."""

from __future__ import annotations

import pytest

from cryo.engines.k8s_security import container_security_context, pod_security_context
from cryo.engines.trino.sql import quote_identifier, quote_qualified_name
from cryo.integrations import alerts
from cryo.scanner.s3_request_cost_ingest import _fq_table
from cryo.security import google_id_token
from cryo.telemetry.ingestion import TrinoQueryIngester, _require_identifier


def test_quote_identifier_escapes_embedded_quotes() -> None:
    assert quote_identifier("events") == '"events"'
    assert quote_identifier('a"; DROP TABLE x; --') == '"a""; DROP TABLE x; --"'


def test_quote_qualified_name_leaves_plain_parts_and_quotes_the_rest() -> None:
    assert quote_qualified_name("iceberg", "analytics", "events") == "iceberg.analytics.events"
    assert quote_qualified_name("iceberg", "my-schema", 'evil"tbl') == (
        'iceberg."my-schema"."evil""tbl"'
    )


def test_s3_log_table_reference_escapes_schema() -> None:
    assert _fq_table("hive", 's3"logs', "s3logs_bucket") == 'hive."s3""logs"."s3logs_bucket"'


@pytest.mark.parametrize("bad", ["runtime; DROP TABLE x", "a b", "", "x.", "1abc"])
def test_trino_query_ingester_rejects_non_identifier_schema(bad: str) -> None:
    with pytest.raises(ValueError, match="identifier"):
        TrinoQueryIngester("http://trino:8080", schema=bad)


def test_trino_query_ingester_accepts_default_identifiers() -> None:
    ingester = TrinoQueryIngester("http://trino:8080")
    assert (ingester.catalog, ingester.schema) == ("system", "runtime")


def test_query_log_table_name_validation() -> None:
    assert _require_identifier("main.query_log", what="t") == "main.query_log"
    with pytest.raises(ValueError, match="identifier"):
        _require_identifier("logs; DROP TABLE logs", what="t")


def test_alert_webhook_rejects_non_http_schemes() -> None:
    with pytest.raises(ValueError, match="http or https"):
        alerts._post_json("file:///etc/passwd", {"text": "x"})


def test_google_tokeninfo_url_encodes_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    class _Response:
        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def read(self) -> bytes:
            return b"{}"

    def fake_urlopen(url: str, timeout: int) -> _Response:
        seen.append(url)
        return _Response()

    monkeypatch.setattr(google_id_token, "urlopen", fake_urlopen)
    google_id_token._fetch_tokeninfo("abc&client_id=evil#frag")

    assert seen == [
        "https://oauth2.googleapis.com/tokeninfo?id_token=abc%26client_id%3Devil%23frag"
    ]


def test_runtime_job_security_contexts_block_privilege_escalation() -> None:
    container = container_security_context()
    assert container["allowPrivilegeEscalation"] is False
    assert container["capabilities"] == {"drop": ["ALL"]}
    assert pod_security_context() == {"seccompProfile": {"type": "RuntimeDefault"}}
    container["capabilities"]["drop"].append("mutated")
    assert container_security_context()["capabilities"] == {"drop": ["ALL"]}
