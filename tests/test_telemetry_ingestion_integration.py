"""Integration tests for multi-engine telemetry ingestion."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from sqlalchemy import select

from cryo.db.models import QueryLog, Source, Table
from cryo.telemetry.ingestion import (
    AthenaIngester,
    DuckDBIngester,
    SparkHistoryIngester,
    ingest_from_query_events,
)


def _seed_source(session) -> Source:
    source = Source(
        id=uuid4(),
        name=f"source-{uuid4()}",
        type="rest",
        config={"catalog_name": "iceberg"},
    )
    session.add(source)
    session.flush()
    return source


def _seed_table(session, *, schema_name: str, table_name: str) -> Table:
    source = _seed_source(session)
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name=schema_name,
        table_name=table_name,
        is_active=True,
    )
    session.add(table)
    session.flush()
    return table


def test_spark_history_ingester_builds_bearer_auth_headers():
    """SparkHistoryIngester should inject Bearer token into request headers."""
    ingester = SparkHistoryIngester(
        "http://spark-history:18080",
        bearer_token="test-token",
        headers={"X-Custom": "cryo"},
    )

    headers = ingester._build_auth_headers()

    assert headers["Authorization"] == "Bearer test-token"
    assert headers["X-Custom"] == "cryo"


def test_spark_history_ingester_passes_auth_headers_to_http_client():
    """fetch_recent_queries should forward auth headers on API calls."""
    ingester = SparkHistoryIngester(
        "http://spark-history:18080",
        bearer_token="secret",
    )

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = []
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("cryo.telemetry.ingestion.httpx.AsyncClient", return_value=mock_client):
        events = asyncio.run(ingester.fetch_recent_queries(limit=10))

    assert events == []
    mock_client.get.assert_called_once()
    call_kwargs = mock_client.get.call_args.kwargs
    assert call_kwargs["headers"]["Authorization"] == "Bearer secret"


def test_athena_ingester_paginates_and_batches_query_executions():
    """AthenaIngester should paginate list_query_executions and batch_get details."""
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = [
        {"QueryExecutionIds": [f"id-{i}" for i in range(55)]},
    ]

    mock_athena = MagicMock()
    mock_athena.get_paginator.return_value = mock_paginator

    submission_time = datetime.now(UTC)
    completion_time = datetime.now(UTC)

    def batch_get_side_effect(QueryExecutionIds: list[str]) -> dict:
        return {
            "QueryExecutions": [
                {
                    "QueryExecutionId": qid,
                    "Status": {
                        "State": "SUCCEEDED",
                        "SubmissionDateTime": submission_time,
                        "CompletionDateTime": completion_time,
                    },
                    "Query": f"SELECT 1 /* {qid} */",
                    "Statistics": {"DataScannedInBytes": 1024},
                    "ResultConfiguration": {"OutputLocation": "s3://bucket/"},
                }
                for qid in QueryExecutionIds
            ]
        }

    mock_athena.batch_get_query_execution.side_effect = batch_get_side_effect

    mock_session = MagicMock()
    mock_session.client.return_value = mock_athena

    ingester = AthenaIngester(boto_session=mock_session)
    events = ingester.fetch_recent_queries(limit=100)

    assert len(events) == 55
    assert mock_athena.batch_get_query_execution.call_count == 2
    first_batch_ids = mock_athena.batch_get_query_execution.call_args_list[0].kwargs[
        "QueryExecutionIds"
    ]
    assert len(first_batch_ids) == 50


def test_duckdb_ingester_falls_back_to_history_file(tmp_path):
    """DuckDBIngester should read plain-text history when DB sources are unavailable."""
    history_file = tmp_path / "history"
    history_file.write_text(
        "-- comment\n"
        "SELECT 1;\n"
        "SELECT COUNT(*) FROM analytics.events;\n"
    )

    ingester = DuckDBIngester(
        history_file=str(history_file),
        query_log_table=None,
        duckdb_database=None,
    )

    events = ingester.fetch_recent_queries(limit=10)

    assert len(events) == 2
    assert events[0]["engine"] == "duckdb"
    assert "SELECT COUNT(*)" in events[1]["query"]


def test_ingest_from_query_events_records_lineage(session):
    """ingest_from_query_events should invoke record_lineage_from_query for matched tables."""
    table = _seed_table(session, schema_name="analytics", table_name="events")
    source = session.get(Source, table.source_id)
    assert source is not None

    events = [
        {
            "query_id": "spark-1",
            "query": "SELECT id FROM analytics.events WHERE dt = '2024-01-01'",
            "engine": "spark",
            "schema_name": "analytics",
            "table_name": "events",
            "executed_at": datetime.now(UTC),
            "duration_ms": 1200,
            "job_name": "daily_etl",
        }
    ]

    with patch("cryo.lineage.tracker.record_lineage_from_query") as mock_record:
        mock_record.return_value = 1
        ingested = asyncio.run(
            ingest_from_query_events(
                session,
                str(source.id),
                events,
                record_lineage=True,
            )
        )

    assert ingested == 1
    mock_record.assert_called_once()
    call_args = mock_record.call_args
    assert call_args.args[2] == table.id
    assert call_args.kwargs["job_name"] == "daily_etl"

    log = session.scalar(select(QueryLog).where(QueryLog.query_id == "spark-1"))
    assert log is not None
    assert log.table_id == table.id
    assert log.engine == "spark"
