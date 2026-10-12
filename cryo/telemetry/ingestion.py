"""Query log ingestion from Trino and other engines."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import QueryLog, Source, Table
from cryo.telemetry.parser import extract_query_ranges, parse_query_predicates

logger = logging.getLogger(__name__)

_DOTTED_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*")


def _require_identifier(name: str, *, what: str) -> str:
    """Reject config-supplied names that can't be interpolated into SQL as bare identifiers."""
    if not _DOTTED_IDENTIFIER_RE.fullmatch(name):
        raise ValueError(f"invalid {what} identifier: {name!r}")
    return name


class TrinoQueryError(RuntimeError):
    """Trino accepted the HTTP request but the query itself failed (bad SQL, permission
    denied, COLUMN_NOT_FOUND, ...). Trino's /v1/statement API always returns HTTP 200 for
    this case, so callers must check the JSON body's "error" key -- see the comment in
    TrinoQueryIngester.fetch_recent_queries for the incident this fixed ().
    """

    def __init__(self, trino_error: dict[str, Any]) -> None:
        self.trino_error = trino_error
        message = trino_error.get("message", str(trino_error))
        error_name = trino_error.get("errorName", "UNKNOWN")
        super().__init__(f"Trino query failed ({error_name}): {message}")


class TrinoQueryIngester:
    """Ingest query logs from Trino's system.runtime.queries table."""

    def __init__(
        self,
        trino_url: str,
        *,
        user: str = "cryo",
        password: str | None = None,
        catalog: str = "system",
        schema: str = "runtime",
        headers: dict[str, str] | None = None,
        verify_ssl: bool = True,
    ):
        self.trino_url = trino_url.rstrip("/")
        self.user = user
        # X-Trino-User alone only authenticates against a coordinator with no auth enabled
        # (trusted-network mode). A coordinator with password/LDAP auth enabled (this call site's
        # original bug: telemetry_trino_user/password were documented and Field()-defined in
        # config.py but never actually passed to this class) needs real HTTP Basic Auth too.
        self.auth = (user, password) if password else None
        self.catalog = _require_identifier(catalog, what="catalog")
        self.schema = _require_identifier(schema, what="schema")
        self.headers = headers or {}
        self.verify_ssl = verify_ssl

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

        # Query the system.runtime.queries table. Column set is deliberately the minimal
        # subset present since very old Trino releases (query_id, query, state, user,
        # source, created, "end", queued/analysis/planning_time_ms) -- older attempts here
        # also selected execution_time_ms, peak_*_memory_bytes, physical_input_bytes/rows,
        # output_*, written_*, cumulative_memory, completed_splits, cpu_time_ms, and
        # wall_time_ms, none of which this table carries on every Trino version/config
        # (confirmed missing on Trino 480 in prod -- ). Trino rejects the whole
        # query on any unresolvable column (COLUMN_NOT_FOUND), so a single optional column
        # in the SELECT list silently zeroed out every fetch. rows_from_trino_queries()
        # already treats bytes_read/rows_read/files_read as optional (q.get(...) with a
        # None default), so dropping them here just means those fields stay unset rather
        # than breaking ingestion entirely.
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
            planning_time_ms
        FROM {self.catalog}.{self.schema}.queries
        WHERE state = 'FINISHED'
          AND created >= TIMESTAMP '{since.strftime("%Y-%m-%d %H:%M:%S")}'
          AND query NOT LIKE '%system.runtime%'
          AND query NOT LIKE '%EXPLAIN%'
        ORDER BY created DESC
        LIMIT {int(limit)}
        """

        async with httpx.AsyncClient(
            timeout=60.0, auth=self.auth, verify=self.verify_ssl
        ) as client:
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

            # Trino's /v1/statement returns HTTP 200 even for a query-level failure (bad
            # SQL, permission denied, ...) -- the error lives in the JSON body's "error"
            # key, not the HTTP status. Without this check, a query-level error page (which
            # has neither "data" nor "nextUri") looked identical to "no more rows" and this
            # method silently returned []. That's what made the earlier COLUMN_NOT_FOUND
            # bug invisible: the curator logged 0 rows fetched, no error, every tick.
            if "error" in result:
                raise TrinoQueryError(result["error"])

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

                if "error" in result:
                    raise TrinoQueryError(result["error"])

            return queries

    async def fetch_query_stats(
        self, query_id: str, *, client: httpx.AsyncClient | None = None
    ) -> dict[str, Any] | None:
        """Fetch a single query's detail from Trino's per-query REST API
        (``/v1/query/{id}``), the only surface this Trino version exposes real scan-byte
        stats on -- confirmed live 2026-09-28: ``system.runtime.queries`` (the bulk table
        ``fetch_recent_queries`` reads) has no ``physical_input_bytes``/rows/files columns
        at all on this deployment (``DESCRIBE`` returns 15 columns, none of them stats;
        that's *why* the  fix narrowed the bulk SELECT to the safe subset -- there
        was nothing to select). ``/v1/query/{id}`` does carry ``queryStats.
        physicalInputDataSize`` (a Trino ``DataSize`` string, e.g. ``"4536702B"``) and
        ``inputs[]`` (real per-table catalog/schema/table identity, more reliable than
        parsing the query's SQL text).

        One HTTP call per query -- callers must bound how many queries they call this for
        (see CuratorTick's curated-subset-only enrichment), never for a whole bulk fetch.

        Accepts an optional shared ``client`` so callers enriching many queries in one tick
        reuse a connection pool instead of opening one per query.
        """
        owns_client = client is None
        http_client = client or httpx.AsyncClient(
            timeout=30.0, auth=self.auth, verify=self.verify_ssl
        )
        try:
            response = await http_client.get(
                f"{self.trino_url}/v1/query/{query_id}",
                headers={"X-Trino-User": self.user},
            )
            if response.status_code == 200:
                return cast(dict[str, Any], response.json())
        except Exception as e:
            logger.warning(f"Failed to fetch query stats for {query_id}: {e}")
        finally:
            if owns_client:
                await http_client.aclose()
        return None


async def ingest_trino_query_log(
    session: Session,
    source: Source,
    ingester: TrinoQueryIngester,
    since: datetime | None = None,
    limit: int = 1000,
) -> int:
    """Fetch recent Trino query logs and hand them off to the Redis buffer.

    In the new tiered pipeline this function is a *pure fetcher* -- it does NOT write to
    Postgres ``query_logs`` directly. The curator drains the Redis buffer on a separate
    cadence, archives rows to Iceberg, and only writes a curated subset back to Postgres.
    That separation is what makes the pipeline scale to 1M+ queries/day: per-query
    Postgres rows are reserved for the small "summary-worthy" subset, and the rest live
    in columnar Iceberg.

    Args:
        session: Database session (used for watermark bookkeeping; no row inserts here)
        source: The Cryo source (lakehouse) these queries are for
        ingester: TrinoQueryIngester instance
        since: Only fetch queries after this time
        limit: Maximum queries to fetch

    Returns:
        Number of raw rows handed off to the Redis buffer.

    The return value is preserved for the legacy scheduler's logging so existing
    dashboards keep working -- the meaning shifts from "rows inserted" to "rows buffered".
    """
    queries = await ingester.fetch_recent_queries(since=since, limit=limit)
    logger.info("Fetched %s queries from Trino (curator will archive + curate)", len(queries))
    if not queries:
        return 0
    try:
        from cryo.telemetry.query_buffer import buffer_trino_queries

        accepted = buffer_trino_queries(queries, source_id=source.id)
        return int(accepted)
    except Exception as exc:  # noqa: BLE001 -- Redis or buffer failures must not break the tick
        logger.warning("Failed to buffer Trino queries to Redis: %s", exc)
        return 0


async def ingest_from_query_events(
    session: Session,
    source_id: str,
    events: list[dict[str, Any]],
    *,
    record_lineage: bool = True,
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
    lineage_candidates: list[tuple[str, Any, str, str | None]] = []
    for event in events:
        query_id = event.get("query_id", str(uuid.uuid4()))
        query_text = event.get("query", event.get("sql", ""))

        # Parse predicates
        predicate_info = parse_query_predicates(query_text) if query_text else None
        query_ranges = extract_query_ranges(query_text) if query_text else []

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
            raw={
                **dict(event.get("raw") or {}),
                "query_ranges": [
                    {"column": item.column, "lower": item.lower, "upper": item.upper}
                    for item in query_ranges
                ],
            },
        )

        session.add(log_entry)
        ingested += 1

        if record_lineage and query_text and table_id:
            lineage_candidates.append(
                (query_text, table_id, query_id, event.get("job_name")),
            )

    session.commit()

    if record_lineage and lineage_candidates:
        from cryo.lineage.tracker import record_lineage_from_query

        for query_text, table_id, query_id, job_name in lineage_candidates:
            try:
                record_lineage_from_query(
                    session,
                    query_text,
                    table_id,
                    job_name=job_name,
                    job_id=query_id,
                )
            except Exception as e:
                logger.warning(f"Failed to record lineage for query {query_id}: {e}")

    return ingested


class SparkHistoryIngester:
    """Ingest query logs from Spark History Server."""

    def __init__(
        self,
        history_server_url: str,
        *,
        basic_auth: tuple[str, str] | None = None,
        bearer_token: str | None = None,
        headers: dict[str, str] | None = None,
    ):
        self.history_server_url = history_server_url.rstrip("/")
        self.basic_auth = basic_auth
        self.bearer_token = bearer_token
        self.headers = headers or {}

    def _build_auth_headers(self) -> dict[str, str]:
        auth_headers: dict[str, str] = dict(self.headers)
        if self.bearer_token:
            auth_headers["Authorization"] = f"Bearer {self.bearer_token}"
        return auth_headers

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
        request_headers = self._build_auth_headers()
        async with httpx.AsyncClient(timeout=60.0, auth=self.basic_auth) as client:
            # Get list of applications
            response = await client.get(
                f"{self.history_server_url}/api/v1/applications",
                headers=request_headers,
            )
            response.raise_for_status()
            apps = response.json()

            # Fetch SQL queries from each app
            for app in apps[:limit]:
                app_id = app.get("id")
                if not app_id:
                    continue

                try:
                    sql_response = await client.get(
                        f"{self.history_server_url}/api/v1/applications/{app_id}/sql",
                        headers=request_headers,
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

        query_execution_ids: list[str] = []
        for page in paginator.paginate(PaginationConfig={"MaxItems": limit}):
            page_ids = page.get("QueryExecutionIds", [])
            if not page_ids:
                continue
            query_execution_ids.extend(page_ids)

        # Fetch execution details in batches (Athena API limit: 50 per call)
        for batch_start in range(0, len(query_execution_ids), 50):
            batch_ids = query_execution_ids[batch_start : batch_start + 50]
            try:
                batch_result = athena.batch_get_query_execution(QueryExecutionIds=batch_ids)
            except Exception as e:
                logger.warning(f"Failed to batch-fetch Athena queries: {e}")
                continue

            for execution in batch_result.get("QueryExecutions", []):
                query_id = execution.get("QueryExecutionId")
                if not query_id:
                    continue

                try:
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
                    logger.warning(f"Failed to process Athena query {query_id}: {e}")
                    continue

        return events


class DuckDBIngester:
    """Ingest query logs from DuckDB query history."""

    def __init__(
        self,
        history_file: str | None = "~/.duckdb_history",
        query_log_table: str | None = None,
        duckdb_database: str | None = None,
    ):
        self.history_file = history_file
        self.query_log_table = query_log_table
        self.duckdb_database = duckdb_database

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
        if since is None:
            since = datetime.now(UTC) - timedelta(hours=24)

        events: list[dict[str, Any]] = []

        if self.query_log_table and self.duckdb_database:
            events.extend(
                self._fetch_from_query_log_table(
                    self.duckdb_database, self.query_log_table, since, limit
                )
            )
            if events:
                return events[:limit]

        if self.duckdb_database:
            events.extend(self._fetch_from_duckdb_database(since, limit))
            if events:
                return events[:limit]

        if self.history_file:
            events.extend(self._fetch_from_history_file(since, limit))

        return events[:limit]

    def _fetch_from_duckdb_database(
        self,
        since: datetime,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Read from DuckDB duckdb_logs view when logging is enabled."""
        import hashlib

        events: list[dict[str, Any]] = []
        try:
            import duckdb
        except ImportError:
            logger.debug("duckdb package not installed; skipping duckdb_logs scan")
            return events

        db_path = self.duckdb_database
        if not db_path:
            return events

        try:
            conn = duckdb.connect(db_path, read_only=True)
            try:
                rows = conn.execute(
                    """
                    SELECT message, timestamp
                    FROM duckdb_logs
                    WHERE type = 'QueryLog'
                      AND timestamp >= ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    [since, limit],
                ).fetchall()
            except Exception:
                logger.debug("duckdb_logs view unavailable in %s", db_path)
                return events
            finally:
                conn.close()

            for message, timestamp in rows:
                if not message:
                    continue
                query_id = hashlib.sha256(f"{message}:{timestamp}".encode()).hexdigest()[:32]
                events.append(
                    {
                        "query_id": query_id,
                        "query_text": str(message),
                        "executed_at": timestamp,
                        "duration_ms": None,
                        "user": "duckdb",
                        "engine": "duckdb",
                        "status": "FINISHED",
                    }
                )
        except Exception as e:
            logger.warning("Failed to read DuckDB logs from %s: %s", db_path, e)

        return events

    def _fetch_from_query_log_table(
        self,
        database_path: str,
        table_name: str,
        since: datetime,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Read from a user-configured query log table."""
        import uuid

        events: list[dict[str, Any]] = []
        try:
            import duckdb
        except ImportError:
            logger.debug("duckdb package not installed; skipping query log table read")
            return events

        try:
            table_name = _require_identifier(table_name, what="query log table")
            conn = duckdb.connect(database_path, read_only=True)
            try:
                rows = conn.execute(
                    f"""
                    SELECT query_text, executed_at, duration_ms
                    FROM {table_name}
                    WHERE executed_at >= ?
                    ORDER BY executed_at DESC
                    LIMIT ?
                    """,
                    [since, limit],
                ).fetchall()

                for query_text, executed_at, duration_ms in rows:
                    if not query_text:
                        continue
                    if isinstance(executed_at, str):
                        executed_at = datetime.fromisoformat(executed_at.replace("Z", "+00:00"))
                    elif executed_at is None:
                        executed_at = datetime.now(UTC)
                    elif executed_at.tzinfo is None:
                        executed_at = executed_at.replace(tzinfo=UTC)

                    events.append(
                        {
                            "query_id": str(uuid.uuid4()),
                            "query": query_text,
                            "engine": "duckdb",
                            "executed_at": executed_at,
                            "duration_ms": duration_ms,
                            "raw": {"source": table_name},
                        }
                    )
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"Failed to read DuckDB query log table {table_name}: {e}")

        return events

    def _fetch_from_history_file(
        self,
        since: datetime,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Fallback: read SQL statements from a plain-text history file."""
        import os
        import uuid

        events: list[dict[str, Any]] = []
        if not self.history_file:
            return events

        history_path = os.path.expanduser(self.history_file)
        if not os.path.exists(history_path):
            logger.warning(f"DuckDB history file not found: {history_path}")
            return events

        try:
            with open(history_path) as f:
                lines = f.readlines()

            for line in lines[-limit:]:
                query = line.strip()
                if not query or query.startswith("--"):
                    continue

                events.append(
                    {
                        "query_id": str(uuid.uuid4()),
                        "query": query,
                        "engine": "duckdb",
                        "executed_at": datetime.now(UTC),
                        "raw": {"query": query, "source": "history_file"},
                    }
                )

        except Exception as e:
            logger.error(f"Failed to read DuckDB history: {e}")

        return events
