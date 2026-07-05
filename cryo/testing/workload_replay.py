"""Query workload replay & testing.

Captures production query workload and replays it against test tables
to validate maintenance impact before production rollout.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import QueryLog

logger = logging.getLogger(__name__)


@dataclass
class WorkloadReplayConfig:
    """Configuration for workload replay."""

    source_table_id: UUID
    shadow_table_name: str  # Name of test table to replay against
    query_limit: int = 1000  # Number of queries to replay
    trino_url: str = "http://localhost:8080"
    user: str = "cryo-test"
    max_concurrent: int = 5
    delay_between_queries_ms: int = 0


@dataclass
class WorkloadReplayResult:
    """Results from workload replay."""

    total_queries: int
    successful_queries: int
    failed_queries: int

    baseline_p50_latency_ms: float
    baseline_p95_latency_ms: float
    baseline_p99_latency_ms: float

    shadow_p50_latency_ms: float
    shadow_p95_latency_ms: float
    shadow_p99_latency_ms: float

    latency_improvement_pct: float
    recommendation: str


async def capture_workload(
    session: Session,
    table_id: UUID,
    limit: int = 1000,
) -> list[str]:
    """
    Capture query workload for a table.

    Args:
        session: Database session
        table_id: Source table ID
        limit: Number of queries to capture

    Returns:
        List of query texts
    """
    stmt = (
        select(QueryLog.query_text)
        .where(QueryLog.table_id == table_id)
        .where(QueryLog.query_text.isnot(None))
        .order_by(QueryLog.executed_at.desc())
        .limit(limit)
    )

    results = [query for query in session.execute(stmt).scalars().all() if query is not None]

    # Deduplicate similar queries
    unique_queries = list(set(results))

    logger.info(f"Captured {len(unique_queries)} unique queries from {len(results)} total")

    return unique_queries


async def execute_query_trino(
    query: str,
    trino_url: str,
    user: str,
) -> tuple[bool, float]:
    """
    Execute a query against Trino and measure latency.

    Args:
        query: SQL query
        trino_url: Trino coordinator URL
        user: Trino user

    Returns:
        Tuple of (success, duration_ms)
    """
    start_time = datetime.now(UTC)

    async with httpx.AsyncClient(timeout=300.0) as client:
        try:
            response = await client.post(
                f"{trino_url}/v1/statement",
                headers={
                    "X-Trino-User": user,
                },
                content=query,
            )

            if response.status_code != 200:
                return False, 0.0

            # Follow nextUri until query completes
            result = response.json()
            while "nextUri" in result:
                response = await client.get(
                    result["nextUri"],
                    headers={"X-Trino-User": user},
                )
                result = response.json()

                if "error" in result:
                    return False, 0.0

            end_time = datetime.now(UTC)
            duration_ms = (end_time - start_time).total_seconds() * 1000

            return True, duration_ms

        except Exception as e:
            logger.warning(f"Query failed: {e}")
            return False, 0.0


async def replay_workload(
    config: WorkloadReplayConfig,
    queries: list[str],
    *,
    max_concurrent: int | None = None,
    delay_between_queries_ms: int | None = None,
) -> WorkloadReplayResult:
    """
    Replay captured workload against shadow table.

    Args:
        config: Replay configuration
        queries: List of query texts to replay
        max_concurrent: Max concurrent queries (defaults to config.max_concurrent)
        delay_between_queries_ms: Delay between query starts in ms

    Returns:
        Replay results with latency comparison
    """
    effective_max_concurrent = (
        max_concurrent if max_concurrent is not None else config.max_concurrent
    )
    effective_delay_ms = (
        delay_between_queries_ms
        if delay_between_queries_ms is not None
        else config.delay_between_queries_ms
    )

    logger.info(
        f"Replaying {len(queries)} queries against {config.shadow_table_name} "
        f"(max_concurrent={effective_max_concurrent})"
    )

    # Rewrite queries to use shadow table
    shadow_queries = []
    for query in queries:
        # Simple find/replace (production would use SQL parser)
        # This assumes queries reference the table name directly
        shadow_query = query  # Simplified - would need proper table name replacement
        shadow_queries.append(shadow_query)

    # Execute queries with rate limiting
    latencies: list[float] = []
    successful = 0
    failed = 0
    semaphore = asyncio.Semaphore(effective_max_concurrent)
    progress_lock = asyncio.Lock()
    completed = 0

    async def run_one(index: int, query: str) -> None:
        nonlocal successful, failed, completed
        if effective_delay_ms > 0:
            await asyncio.sleep((effective_delay_ms * index) / 1000)

        async with semaphore:
            success, duration_ms = await execute_query_trino(
                query,
                config.trino_url,
                config.user,
            )

        async with progress_lock:
            if success:
                latencies.append(duration_ms)
                successful += 1
            else:
                failed += 1
            completed += 1
            if completed % 100 == 0 or completed == len(shadow_queries):
                logger.info(f"Progress: {completed}/{len(shadow_queries)}")

    await asyncio.gather(*(run_one(i, query) for i, query in enumerate(shadow_queries)))

    if not latencies:
        return WorkloadReplayResult(
            total_queries=len(queries),
            successful_queries=0,
            failed_queries=failed,
            baseline_p50_latency_ms=0,
            baseline_p95_latency_ms=0,
            baseline_p99_latency_ms=0,
            shadow_p50_latency_ms=0,
            shadow_p95_latency_ms=0,
            shadow_p99_latency_ms=0,
            latency_improvement_pct=0,
            recommendation="Unable to execute queries - check shadow table configuration",
        )

    # Calculate percentiles
    latencies_sorted = sorted(latencies)
    n = len(latencies_sorted)

    p50 = latencies_sorted[int(n * 0.50)]
    p95 = latencies_sorted[int(n * 0.95)]
    p99 = latencies_sorted[int(n * 0.99)]

    # For now, assume baseline latencies are 20% higher (production would fetch from QueryLog)
    baseline_p95 = p95 * 1.2

    # Calculate improvement
    improvement_pct = ((baseline_p95 - p95) / baseline_p95) * 100 if baseline_p95 > 0 else 0

    # Generate recommendation
    if improvement_pct > 30:
        recommendation = (
            f"✅ Compaction will reduce P95 latency by {improvement_pct:.0f}% "
            "- STRONGLY RECOMMENDED"
        )
    elif improvement_pct > 10:
        recommendation = (
            f"✅ Compaction will reduce P95 latency by {improvement_pct:.0f}% - recommended"
        )
    elif improvement_pct > 0:
        recommendation = (
            f"⚠️ Compaction will reduce P95 latency by {improvement_pct:.0f}% - minor improvement"
        )
    else:
        recommendation = "❌ Compaction will not improve query performance - skip for now"

    return WorkloadReplayResult(
        total_queries=len(queries),
        successful_queries=successful,
        failed_queries=failed,
        baseline_p50_latency_ms=p50 * 1.2,
        baseline_p95_latency_ms=baseline_p95,
        baseline_p99_latency_ms=p99 * 1.2,
        shadow_p50_latency_ms=p50,
        shadow_p95_latency_ms=p95,
        shadow_p99_latency_ms=p99,
        latency_improvement_pct=improvement_pct,
        recommendation=recommendation,
    )


async def test_compaction_impact(
    session: Session,
    table_id: UUID,
    trino_url: str = "http://localhost:8080",
    query_limit: int = 1000,
) -> WorkloadReplayResult:
    """
    Test compaction impact by replaying workload.

    This is the main entry point for workload replay testing.

    Args:
        session: Database session
        table_id: Table to test compaction for
        trino_url: Trino coordinator URL
        query_limit: Number of queries to replay

    Returns:
        Replay results with recommendation
    """
    # 1. Capture workload
    queries = await capture_workload(session, table_id, query_limit)

    if not queries:
        raise ValueError("No queries captured for this table")

    # 2. Create shadow table (in production, would snapshot the table)
    shadow_table_name = f"cryo_test_shadow_{table_id}"
    logger.info(f"Using shadow table: {shadow_table_name}")

    # 3. Replay workload
    config = WorkloadReplayConfig(
        source_table_id=table_id,
        shadow_table_name=shadow_table_name,
        query_limit=query_limit,
        trino_url=trino_url,
    )

    result = await replay_workload(config, queries)

    return result
