"""Read live Kubernetes Job state for Spark-on-K8s capacity accounting.

Mirrors cryo.engines.native.job_state's Move 6 pattern: replaces the Spark-K8s engine's
static ``used=0`` capacity stub (previously ``cryo.governor.capacity._static_capacity``)
with a real count of active dispatch Jobs, so a destination's ``slot_cap`` is enforced
against what Kubernetes actually reports running, not just Cryo's own Redis bookkeeping.

Only Job *count* is tracked, not CPU/memory -- the dispatch Job's own pod (the
spark-submit CLI wrapper) requests a fixed, trivial 250m/512Mi regardless of the real
Spark driver/executor footprint (those are separate pods spark-submit creates itself),
so parsing resources off this Job would be meaningless. slot_cap is a concurrent-Spark-
submission count, not a resource budget, which is exactly what Job count measures.

Graceful degradation: if the ``kubernetes`` Python client is unavailable or no in-cluster
config is loadable, ``list_active_spark_jobs`` returns ``None`` and the caller falls back
to Cryo's own in-flight accounting (same contract as NativeJobStateReader).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from cryo.engines.native.job_state import (
    _in_cluster_configured,
    _load_k8s_client,
    current_k8s_namespace,
)

logger = logging.getLogger(__name__)

_SPARK_JOB_LABEL = "app.kubernetes.io/component=spark-fallback"
_DESTINATION_LABEL = "cryo/destination"


@dataclass(frozen=True)
class SparkJobState:
    """One active Spark-on-K8s dispatch Job (see cryo.engines.spark.k8s._submit)."""

    name: str
    namespace: str
    destination_id: str | None
    active_pods: int


class SparkJobStateReader:
    """Reads live state of cryo Spark-on-K8s dispatch Jobs.

    These are the wrapping batch/v1 Jobs that run `spark-submit`, not the Spark
    driver/executor pods spark-submit spawns itself -- one dispatch Job in flight is
    exactly one `execute()` call consuming one of the destination's slot_cap slots.
    """

    def __init__(self, *, namespace: str | None = None, batch_api: Any | None = None) -> None:
        self._namespace = namespace or current_k8s_namespace()
        self._batch_api = batch_api

    def is_available(self) -> bool:
        """True when live K8s reads are possible."""
        if self._batch_api is not None:
            return True
        return _in_cluster_configured()

    @property
    def namespace(self) -> str:
        return self._namespace

    def list_active_spark_jobs(
        self,
        *,
        destination_id: str | None = None,
    ) -> tuple[SparkJobState, ...] | None:
        """List currently active Spark-on-K8s dispatch Jobs.

        Returns ``None`` when the kubernetes client is not available (signals the caller
        to fall back). Returns an empty tuple when K8s is reachable but no matching Jobs
        exist.
        """
        batch_api = self._batch_api
        if batch_api is None:
            client_mod = _load_k8s_client()
            if client_mod is None or not _in_cluster_configured():
                return None
            batch_api = client_mod.BatchV1Api()
            self._batch_api = batch_api

        try:
            jobs = batch_api.list_namespaced_job(
                namespace=self._namespace,
                label_selector=_SPARK_JOB_LABEL,
            )
        except Exception as exc:
            logger.warning(
                "Failed to list Spark-on-K8s Jobs in namespace %s: %s", self._namespace, exc
            )
            return None

        results: list[SparkJobState] = []
        for job in getattr(jobs, "items", []) or []:
            status = getattr(job, "status", None)
            active = int(getattr(status, "active", 0) or 0) if status is not None else 0
            succeeded = int(getattr(status, "succeeded", 0) or 0) if status is not None else 0
            failed = int(getattr(status, "failed", 0) or 0) if status is not None else 0
            if succeeded > 0 or failed > 0:
                # Pending/unschedulable Jobs still reserve admission capacity;
                # only terminal Jobs stop consuming a slot.
                continue

            metadata = getattr(job, "metadata", None)
            labels = getattr(metadata, "labels", None) or {}
            job_destination = labels.get(_DESTINATION_LABEL)
            if destination_id and job_destination and job_destination != destination_id:
                continue

            results.append(
                SparkJobState(
                    name=str(getattr(metadata, "name", "")),
                    namespace=str(getattr(metadata, "namespace", self._namespace)),
                    destination_id=str(job_destination) if job_destination else None,
                    active_pods=active,
                )
            )

        return tuple(results)
