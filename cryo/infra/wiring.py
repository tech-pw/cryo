from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from typing import Any

from cryo.config import Settings, get_settings
from cryo.db.repositories.job_store import SQLJobStore
from cryo.db.repositories.scheduler_store import SQLSchedulerStore
from cryo.db.session import SessionLocal
from cryo.queue.locks import RedisLockStore
from cryo.queue.redis_queue import RedisMaintenanceQueue
from cryo.queue.run_events import NullRunEventSink, RedisRunEventBus, RunEventBus


@dataclass
class RuntimeWiring:
    settings: Settings
    queue: RedisMaintenanceQueue
    lock_store: RedisLockStore
    job_store: SQLJobStore
    scheduler_store: SQLSchedulerStore
    holder_id: str
    redis_client: Any
    run_events: RunEventBus | NullRunEventSink

    def is_scheduler_leader(self, ttl_seconds: int = 30) -> bool:
        return self.lock_store.acquire_leader("scheduler", self.holder_id, ttl_seconds)

    def is_governor_leader(self, ttl_seconds: int = 30) -> bool:
        return self.lock_store.acquire_leader("governor", self.holder_id, ttl_seconds)


def build_runtime_wiring(settings: Settings | None = None) -> RuntimeWiring:
    settings = settings or get_settings()
    redis_client = _redis_client(settings.redis_url)
    queue = RedisMaintenanceQueue(redis_client)
    lock_store = RedisLockStore(redis_client)
    session_factory = SessionLocal
    holder_id = f"{socket.gethostname()}:{os.getpid()}"
    return RuntimeWiring(
        settings=settings,
        queue=queue,
        lock_store=lock_store,
        job_store=SQLJobStore(session_factory),
        scheduler_store=SQLSchedulerStore(session_factory, redis_client),
        holder_id=holder_id,
        redis_client=redis_client,
        run_events=RedisRunEventBus(redis_client),
    )


def _redis_client(url: str):  # type: ignore[no-untyped-def]
    import redis

    return redis.from_url(url, decode_responses=False)
