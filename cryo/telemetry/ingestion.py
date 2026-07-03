"""Query log ingestion from Trino and other engines."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import QueryLog, Source, Table
from cryo.telemetry.parser import extract_table_from_query, parse_query_predicates

logger = logging.getLogger(__name__)


class TrinoQueryIngester:
    """Ingest query logs from Trino's system.runtime.queries table."""

    def __init__(
        self,
        trino_url: str,
        *,
        user: str = "cryo",
        catalog: str = "system",
        schema: str = "runtime",
        headers: dict[str, str] | None = None,
    ):
        self.trino_url = trino_url.rstrip("/")
        self.user = user
        self.catalog = catalog
        self.schema = schema
        self.headers = headers or {}

    async def fetch_recent_queries(
        self,
        since: datetime | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Fetch recent queries from Trino's system tables.

        Args:
            since: Only fetch queries executed after this time
            limit: Maximum number of queries to fetch

        Returns:
            List of query records from Trino
        """
        if since is None:
            since = datetime.now(UTC) - timedelta(hours=24)

        # Query the system.runtime.queries table
        sql = f"""
        SELECT
            query_id,
            query,
            state,
            user AS user_name,
            source,
            created AS executed_at,
            "end" AS ended_at,
            queued_time_ms,
            analysis_time_ms,
            planning_time_ms,
            execution_time_ms,
            peak_user_memory_bytes,
            peak_total_memory_bytes,
            physical_input_bytes,
            physical_input_rows,
            output_bytes,
            output_rows,
            written_bytes,
            written_rows,
            cumulative_memory,
            completed_splits,
            cpu_time_ms,
            wall_time_ms
        FROM {self.catalog}.{self.schema}.queries
        WHERE state = 'FINISHED'
          AND created >= TIMESTAMP '{since.strftime("%Y-%m-%d %H:%M:%S")}'
          AND query NOT LIKE '%system.runtime%'
          AND query NOT LIKE '%EXPLAIN%'
        ORDER BY created DESC
        LIMIT {limit}
        """

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self.trino_url}/v1/statement",
                headers={
                    "X-Trino-User": self.user,
                    "X-Trino-Catalog": self.catalog,
                    "X-Trino-Schema": self.schema,
                    **self.headers,
                },
                content=sql,
            )
            response.raise_for_status()

            result = response.json()
            queries = []

            # Trino returns results in pages, follow nextUri
            while True:
                if "data" in result:
                    columns = [c["name"] for c in result.get("columns", [])]
                    for row in result["data"]:
                        queries.append(dict(zip(columns, row, strict=False)))

                if "nextUri" not in result:
                    break

                response = await client.get(
                    result["nextUri"],
                    headers={"X-Trino-User": self.user},
                )
                response.raise_for_status()
                result = response.json()

            return queries

    async def fetch_query_stats(self, query_id: str) -> dict[str, Any] | None:
        """Fetch detailed stats for a specific query.

        This uses the query info API to get file-level statistics.
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(
                    f"{self.trino_url}/v1/query/{query_id}",
                    headers={"X-Trino-User": self.user},
                )
                if response.status_code == 200:
                    return cast(dict[str, Any], response.json())
            except Exception as e:
                logger.warning(f"Failed to fetch query stats for {query_id}: {e}")
        return None


async def ingest_trino_query_log(
    session: Session,
    source: Source,
    ingester: TrinoQueryIngester,
    since: datetime | None = None,
    limit: int = 1000,
) -> int:
    """Ingest Trino query logs into the database.

    Args:
        session: Database session
        source: The Cryo source (lakehouse) these queries are for
        ingester: TrinoQueryIngester instance
        since: Only ingest queries after this time
        limit: Maximum queries to ingest

    Returns:
        Number of queries ingested
    """
    # Get existing query IDs to avoid duplicates
    existing_ids = set(
        session.scalars(select(QueryLog.query_id).where(QueryLog.source_id == source.id))
    )

    # Fetch queries from Trino
    queries = await ingester.fetch_recent_queries(since=since, limit=limit)
    logger.info(f"Fetched {len(queries)} queries from Trino")

    # Build a lookup of tables by schema.table_name
    tables = session.scalars(
        select(Table).where(Table.source_id == source.id, Table.is_active.is_(True))
    ).all()
    table_lookup: dict[str, Table] = {}
    for t in tables:
        key = f"{t.schema_name}.{t.table_name}".lower()
        table_lookup[key] = t

    ingested = 0
    for q in queries:
        query_id = q.get("query_id")
        if not query_id or query_id in existing_ids:
            continue

        query_text = q.get("query", "")
        if not query_text:
            continue

        # Parse the query to extract predicates
        predicate_info = parse_query_predicates(query_text)

        # Try to match to a known table
        schema_name, table_name = extract_table_from_query(query_text)
        table_id = None
        if schema_name and table_name:
            key = f"{schema_name}.{table_name}".lower()
            if key in table_lookup:
                table_id = table_lookup[key].id

        # Parse execution time
        executed_at = q.get("executed_at")
        if isinstance(executed_at, str):
            try:
                executed_at = datetime.fromisoformat(executed_at.replace("Z", "+00:00"))
            except ValueError:
                executed_at = datetime.now(UTC)
        elif executed_at is None:
            executed_at = datetime.now(UTC)

        # Calculate total duration
        duration_ms = (
            (q.get("queued_time_ms") or 0)
            + (q.get("analysis_time_ms") or 0)
            + (q.get("planning_time_ms") or 0)
            + (q.get("execution_time_ms") or 0)
        )

        log_entry = QueryLog(
            source_id=source.id,
            table_id=table_id,
            query_id=query_id,
            engine="trino",
            query_text=query_text[:10000] if len(query_text) > 10000 else query_text,
            query_type=predicate_info.query_type,
            filter_columns=[
                {
                    "column": c.column,
                    "operator": c.operator,
                    "is_equality": c.is_equality,
                    "is_range": c.is_range,
                }
                for c in predicate_info.filter_columns
            ],
            join_columns=[{"column": c} for c in predicate_info.join_columns],
            projection_columns=[{"column": c} for c in predicate_info.projection_columns],
            executed_at=executed_at,
            duration_ms=duration_ms,
            rows_read=q.get("physical_input_rows"),
            bytes_read=q.get("physical_input_bytes"),
            user_name=q.get("user_name"),
            raw={
                k: v
                for k, v in q.items()
                if k
                not in (
                    "query",
                    "query_id",
                    "user_name",
                    "executed_at",
                    "physical_input_rows",
                    "physical_input_bytes",
                )
            },
        )

        session.add(log_entry)
        ingested += 1

    session.commit()
    logger.info(f"Ingested {ingested} new query logs")
    return ingested


async def ingest_from_query_events(
    session: Session,
    source_id: str,
    events: list[dict[str, Any]],
) -> int:
    """Ingest query logs from generic event format.

    This allows ingestion from various sources (Spark history, custom events, etc.)

    Args:
        session: Database session
        source_id: The Cryo source ID
        events: List of query event dictionaries with standard fields

    Returns:
        Number of events ingested
    """
    import uuid

    source_uuid = uuid.UUID(source_id)

    # Build table lookup
    tables = session.scalars(
        select(Table).where(Table.source_id == source_uuid, Table.is_active.is_(True))
    ).all()
    table_lookup: dict[str, Table] = {}
    for t in tables:
        key = f"{t.schema_name}.{t.table_name}".lower()
        table_lookup[key] = t

    ingested = 0
    for event in events:
        query_id = event.get("query_id", str(uuid.uuid4()))
        query_text = event.get("query", event.get("sql", ""))

        # Parse predicates
        predicate_info = parse_query_predicates(query_text) if query_text else None

        # Match table
        schema_name = event.get("schema_name")
        table_name = event.get("table_name")
        table_id = None
        if schema_name and table_name:
            key = f"{schema_name}.{table_name}".lower()
            if key in table_lookup:
                table_id = table_lookup[key].id

        log_entry = QueryLog(
            source_id=source_uuid,
            table_id=table_id,
            query_id=query_id,
            engine=event.get("engine", "unknown"),
            query_text=query_text[:10000] if query_text and len(query_text) > 10000 else query_text,
            query_type=predicate_info.query_type if predicate_info else event.get("query_type"),
            filter_columns=[
                {
                    "column": c.column,
                    "operator": c.operator,
                    "is_equality": c.is_equality,
                    "is_range": c.is_range,
                }
                for c in predicate_info.filter_columns
            ]
            if predicate_info
            else event.get("filter_columns"),
            join_columns=[{"column": c} for c in predicate_info.join_columns]
            if predicate_info
            else event.get("join_columns"),
            projection_columns=[{"column": c} for c in predicate_info.projection_columns]
            if predicate_info
            else event.get("projection_columns"),
            executed_at=event.get("executed_at", datetime.now(UTC)),
            duration_ms=event.get("duration_ms"),
            rows_read=event.get("rows_read"),
            bytes_read=event.get("bytes_read"),
            files_read=event.get("files_read"),
            files_pruned=event.get("files_pruned"),
            user_name=event.get("user"),
            raw=event.get("raw"),
        )

        session.add(log_entry)
        ingested += 1

    session.commit()
    return ingested


class SparkHistoryIngester:
    """Ingest query logs from Spark History Server."""

    def __init__(self, history_server_url: str):
        self.history_server_url = history_server_url.rstrip("/")

    async def fetch_recent_queries(
        self,
        since: datetime | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Fetch recent SQL queries from Spark History Server.

        Args:
            since: Only fetch queries executed after this time
            limit: Maximum number of queries to fetch

        Returns:
            List of normalized query events
        """
        if since is None:
            since = datetime.now(UTC) - timedelta(hours=24)

        events: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=60.0) as client:
            # Get list of applications
            response = await client.get(f"{self.history_server_url}/api/v1/applications")
            response.raise_for_status()
            apps = response.json()

            # Fetch SQL queries from each app
            for app in apps[:limit]:
                app_id = app.get("id")
                if not app_id:
                    continue

                try:
                    sql_response = await client.get(
                        f"{self.history_server_url}/api/v1/applications/{app_id}/sql"
                    )
                    if sql_response.status_code != 200:
                        continue

                    sql_queries = sql_response.json()
                    for query in sql_queries:
                        execution_id = query.get("id")
                        description = query.get("description", "")
                        duration_ms = query.get("duration", 0)
                        submission_time = query.get("submissionTime")

                        if submission_time:
                            executed_at = datetime.fromtimestamp(submission_time / 1000, tz=UTC)
                            if executed_at < since:
                                continue
                        else:
                            executed_at = datetime.now(UTC)

                        events.append(
                            {
                                "query_id": f"{app_id}-{execution_id}",
                                "query": description,
                                "engine": "spark",
                                "executed_at": executed_at,
                                "duration_ms": duration_ms,
                                "raw": query,
                            }
                        )
                except Exception as e:
                    logger.warning(f"Failed to fetch SQL from app {app_id}: {e}")
                    continue

        return events


class AthenaIngester:
    """Ingest query logs from AWS Athena."""

    def __init__(self, boto_session: Any = None):
        """Initialize Athena ingester.

        Args:
            boto_session: boto3.Session instance (optional)
        """
        self.boto_session = boto_session

    def fetch_recent_queries(
        self,
        since: datetime | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Fetch recent queries from Athena.

        Args:
            since: Only fetch queries executed after this time
            limit: Maximum number of queries to fetch

        Returns:
            List of normalized query events
        """
        import boto3

        if since is None:
            since = datetime.now(UTC) - timedelta(hours=24)

        session = self.boto_session or boto3.Session()
        athena = session.client("athena")

        events: list[dict[str, Any]] = []
        paginator = athena.get_paginator("list_query_executions")

        for page in paginator.paginate(PaginationConfig={"MaxItems": limit}):
            query_execution_ids = page.get("QueryExecutionIds", [])
            if not query_execution_ids:
                break

            # Get execution details in batches
            for query_id in query_execution_ids:
                try:
                    result = athena.get_query_execution(QueryExecutionId=query_id)
                    execution = result.get("QueryExecution", {})

                    status = execution.get("Status", {})
                    state = status.get("State")
                    if state != "SUCCEEDED":
                        continue

                    submission_time = status.get("SubmissionDateTime")
                    completion_time = status.get("CompletionDateTime")

                    if submission_time and submission_time < since:
                        continue

                    query = execution.get("Query", "")
                    stats = execution.get("Statistics", {})

                    duration_ms = None
                    if submission_time and completion_time:
                        duration_ms = int(
                            (completion_time - submission_time).total_seconds() * 1000
                        )

                    events.append(
                        {
                            "query_id": query_id,
                            "query": query,
                            "engine": "athena",
                            "executed_at": submission_time or datetime.now(UTC),
                            "duration_ms": duration_ms,
                            "bytes_read": stats.get("DataScannedInBytes"),
                            "user": execution.get("ResultConfiguration", {}).get("OutputLocation"),
                            "raw": execution,
                        }
                    )

                except Exception as e:
                    logger.warning(f"Failed to fetch Athena query {query_id}: {e}")
                    continue

        return events


class DuckDBIngester:
    """Ingest query logs from DuckDB local history file."""

    def __init__(self, history_file: str = "~/.duckdb/history"):
        self.history_file = history_file

    def fetch_recent_queries(
        self,
        since: datetime | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Fetch recent queries from DuckDB history file.

        Args:
            since: Only fetch queries executed after this time
            limit: Maximum number of queries to fetch

        Returns:
            List of normalized query events
        """
        import os
        import uuid

        if since is None:
            since = datetime.now(UTC) - timedelta(hours=24)

        events: list[dict[str, Any]] = []
        history_path = os.path.expanduser(self.history_file)

        if not os.path.exists(history_path):
            logger.warning(f"DuckDB history file not found: {history_path}")
            return events

        try:
            with open(history_path) as f:
                lines = f.readlines()

            # DuckDB history is just SQL statements, one per line
            for line in lines[-limit:]:
                query = line.strip()
                if not query or query.startswith("--"):
                    continue

                events.append(
                    {
                        "query_id": str(uuid.uuid4()),
                        "query": query,
                        "engine": "duckdb",
                        "executed_at": datetime.now(UTC),  # No timestamp in history file
                        "raw": {"query": query},
                    }
                )

        except Exception as e:
            logger.error(f"Failed to read DuckDB history: {e}")

        return events
