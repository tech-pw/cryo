"""State of one Spark-on-K8s statement dispatched asynchronously (#435).

Kept free of Kubernetes imports so the worker and the governor resolver can read it
without loading the Spark client.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SparkAsyncState:
    """Where one async Spark statement stands between worker passes.

    ``ref`` is the Spark Job in flight, or None while a catalog retry waits until
    ``not_before``. The retry counters carry SparkK8sEngine.execute()'s own retry budget
    across passes, so the async path retries exactly as the blocking one does.
    ``deadline_seconds`` is the table-size-capped Job deadline the blocking path would have
    waited for.
    """

    started_at: float
    ref: str | None = None
    submitted_at: float = 0.0
    deadline_seconds: int | None = None
    attempt: int = 0
    oom_steps: int = 0
    executor_oom_steps: int = 0
    catalog_attempts: int = 0
    not_before: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "ref": self.ref,
            "submitted_at": self.submitted_at,
            "deadline_seconds": self.deadline_seconds,
            "attempt": self.attempt,
            "oom_steps": self.oom_steps,
            "executor_oom_steps": self.executor_oom_steps,
            "catalog_attempts": self.catalog_attempts,
            "not_before": self.not_before,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> SparkAsyncState:
        ref = payload.get("ref")
        deadline = payload.get("deadline_seconds")
        not_before = payload.get("not_before")
        return cls(
            started_at=float(payload.get("started_at") or time.time()),
            ref=str(ref) if ref else None,
            submitted_at=float(payload.get("submitted_at") or 0.0),
            deadline_seconds=int(deadline) if deadline is not None else None,
            attempt=int(payload.get("attempt") or 0),
            oom_steps=int(payload.get("oom_steps") or 0),
            executor_oom_steps=int(payload.get("executor_oom_steps") or 0),
            catalog_attempts=int(payload.get("catalog_attempts") or 0),
            not_before=float(not_before) if not_before is not None else None,
        )

    def next_check_at(self, *, now: float, poll_interval: float) -> float:
        """When the resolver should next look: after the backoff, else after one poll."""
        if self.ref is None:
            return self.not_before if self.not_before is not None else now
        return now + poll_interval

    def ready_to_resume(self, *, now: float, run_terminal: bool) -> bool:
        """The worker has something to do: a Job ended, its deadline passed, or a retry is due."""
        if self.ref is None:
            return self.not_before is None or now >= self.not_before
        if run_terminal:
            return True
        return self.deadline_seconds is not None and now - self.submitted_at >= (
            self.deadline_seconds
        )
