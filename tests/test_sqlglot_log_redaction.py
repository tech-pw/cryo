"""sqlglot must not copy raw query text, literals included, into Cryo's logs (#165).

sqlglot logs any statement it cannot fully parse (``PREPARE ... FROM`` from BI tools, for
example) verbatim at WARNING. Query-log SQL is user data, so Cryo raises the ``sqlglot``
logger to ERROR in logging setup and in every module that parses query text.
"""

from __future__ import annotations

import logging

import pytest
import sqlglot

from cryo.observability.logging import quiet_sqlglot_logger, quiet_third_party_loggers

_SECRET = "alice@example.com"
_UNSUPPORTED = f"PREPARE s1 FROM SELECT * FROM t WHERE email = '{_SECRET}'"


@pytest.fixture()
def loud_sqlglot():
    logger = logging.getLogger("sqlglot")
    previous = logger.level
    logger.setLevel(logging.NOTSET)
    yield logger
    logger.setLevel(previous)


def _sqlglot_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name.startswith("sqlglot")]


def test_unquieted_sqlglot_would_log_the_literal(loud_sqlglot, caplog) -> None:
    """Guards the test itself: without the fix sqlglot really does leak the literal."""
    caplog.set_level(logging.INFO)
    sqlglot.parse_one(_UNSUPPORTED, read="trino")
    assert any(_SECRET in record.getMessage() for record in _sqlglot_records(caplog))


@pytest.mark.parametrize("quiet", [quiet_sqlglot_logger, quiet_third_party_loggers])
def test_quieted_sqlglot_logs_nothing_for_unsupported_syntax(loud_sqlglot, caplog, quiet) -> None:
    quiet()
    caplog.set_level(logging.INFO)
    sqlglot.parse_one(_UNSUPPORTED, read="trino")
    assert loud_sqlglot.level == logging.ERROR
    assert not _sqlglot_records(caplog)


def test_cryo_query_parsers_never_leak_the_literal(loud_sqlglot, caplog) -> None:
    """Every Cryo entry point that hands query text to sqlglot quiets it first, so the
    statement stays out of the logs even in processes that skipped logging setup."""
    import uuid

    from cryo.lineage.tracker import parse_query_for_lineage
    from cryo.planner.partition_query_affinity import _matchers_for_query
    from cryo.planner.partitions import PartitionFieldSpec
    from cryo.telemetry.parser import extract_query_ranges, parse_query_predicates

    caplog.set_level(logging.INFO)
    calls = (
        lambda: parse_query_predicates(_UNSUPPORTED),
        lambda: extract_query_ranges(_UNSUPPORTED),
        lambda: parse_query_for_lineage(_UNSUPPORTED, uuid.uuid4()),
        lambda: _matchers_for_query(_UNSUPPORTED, PartitionFieldSpec(base_column="email")),
    )
    for call in calls:
        loud_sqlglot.setLevel(logging.NOTSET)
        call()
        assert loud_sqlglot.level == logging.ERROR
    assert not any(_SECRET in record.getMessage() for record in caplog.records)


def test_quiet_does_not_lower_a_stricter_level(loud_sqlglot) -> None:
    loud_sqlglot.setLevel(logging.CRITICAL)
    quiet_sqlglot_logger()
    assert loud_sqlglot.level == logging.CRITICAL
