"""Read live Kubernetes Job state for native-engine capacity accounting.

The Governor's Move 6 work replaces a static ``slot_cap`` + hardcoded ``used=0`` with
real K8s Job resource state. This module is the read-side: list active Jobs created
by :mod:`cryo.engines.native.job_dispatch` and parse their CPU/memory requests so
admission can distinguish "5 small jobs" from "1 huge job".

Graceful degradation: if the ``kubernetes`` Python client is unavailable or no
in-cluster config is loadable, ``list_active_native_jobs`` returns ``None`` and
the caller falls back to queue-/subprocess-based accounting (preserving the
pre-Move-6 behavior for non-K8s deployments and tests).
"""

from __future__ import annotations

import importlib
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import ModuleType
from typing import Any

logger = logging.getLogger(__name__)


_NATIVE_JOB_LABEL = "app.kubernetes.io/component=native-engine"
_DESTINATION_LABEL = "cryo/destination"


_MILLI_RE = re.compile(r"^\s*(\d+)\s*m\s*$")
_INT_RE = re.compile(r"^\s*(\d+)\s*$")
_FLOAT_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*$")
_MIB_SUFFIX_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*Mi\s*$", re.IGNORECASE)
_MIB_KIB_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*Ki\s*$", re.IGNORECASE)
_MIB_GIB_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*Gi\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class NativeJobState:
    """Resource state for one active cryo native-engine Job."""

    name: str
    namespace: str
    destination_id: str | None
    table_ref: str | None
    cpu_milli: int
    mem_mib: int
    active_pods: int


def _load_k8s_config() -> ModuleType | None:
    try:
        return importlib.import_module("kubernetes.config")
    except ImportError:
        return None


def _load_k8s_client() -> ModuleType | None:
    try:
        return importlib.import_module("kubernetes.client")
    except ImportError:
        return None


def _in_cluster_configured() -> bool:
    k8s_config = _load_k8s_config()
    if k8s_config is None:
        return False
    try:
        k8s_config.load_incluster_config()
    except Exception:
        return False
    return True


def parse_cpu_to_milli(value: Any) -> int:
    """Parse a Kubernetes CPU quantity string (e.g. ``"250m"``, ``"1"``, ``"0.5"``)."""
    if value is None:
        return 0
    text = str(value).strip()
    if not text:
        return 0
    match = _MILLI_RE.match(text)
    if match:
        return int(match.group(1))
    match = _INT_RE.match(text)
    if match:
        return int(match.group(1)) * 1000
    match = _FLOAT_RE.match(text)
    if match:
        return int(float(match.group(1)) * 1000)
    return 0


def parse_memory_to_mib(value: Any) -> int:
    """Parse a Kubernetes memory quantity to MiB (supports Mi, Ki, Gi, plain int)."""
    if value is None:
        return 0
    text = str(value).strip()
    if not text:
        return 0
    match = _MIB_SUFFIX_RE.match(text)
    if match:
        return int(float(match.group(1)))
    match = _MIB_GIB_RE.match(text)
    if match:
        return int(float(match.group(1)) * 1024)
    match = _MIB_KIB_RE.match(text)
    if match:
        return int(float(match.group(1)) / 1024)
    match = _INT_RE.match(text)
    if match:
        # Plain integer is interpreted as bytes (Kubernetes default for memory).
        return int(match.group(1)) // (1024 * 1024)
    match = _FLOAT_RE.match(text)
    if match:
        return int(float(match.group(1)) // (1024 * 1024))
    return 0


def _read_legacy_env(
    payload: Mapping[str, Any] | None,
) -> tuple[int, int]:
    """Read CPU/mem from a legacy payload (used when pod spec is unavailable)."""
    if not payload:
        return 0, 0
    return parse_cpu_to_milli(payload.get("cpu")), parse_memory_to_mib(payload.get("memory"))


def _extract_job_resources(job: Any) -> tuple[int, int]:
    """Return (cpu_milli, mem_mib) from a Job's first container in its pod template.

    Falls back to the Job's annotations (``cryo/cpu`` / ``cryo/memory``) if present,
    which the dispatcher sets for observability.
    """
    annotations: Mapping[str, Any] = {}
    if getattr(job, "metadata", None) is not None:
        annotations = getattr(job.metadata, "annotations", None) or {}

    cpu_milli = parse_cpu_to_milli(annotations.get("cryo/cpu"))
    mem_mib = parse_memory_to_mib(annotations.get("cryo/memory"))
    if cpu_milli or mem_mib:
        return cpu_milli, mem_mib

    spec = getattr(job, "spec", None)
    template = getattr(spec, "template", None) if spec is not None else None
    pod_spec = getattr(template, "spec", None) if template is not None else None
    containers = getattr(pod_spec, "containers", None) if pod_spec is not None else None
    if containers:
        container = containers[0]
        resources = getattr(container, "resources", None)
        if resources is not None:
            requests = getattr(resources, "requests", None) or {}
            return (
                parse_cpu_to_milli(requests.get("cpu")),
                parse_memory_to_mib(requests.get("memory")),
            )
    return 0, 0


class NativeJobStateReader:
    """Reads live state of cryo native-engine Kubernetes Jobs.

    All methods degrade to empty/zero results when the kubernetes client is
    unavailable or no in-cluster config is loadable.
    """

    def __init__(
        self,
        *,
        namespace: str = "default",
        batch_api: Any | None = None,
    ) -> None:
        self._namespace = namespace
        self._batch_api = batch_api

    def is_available(self) -> bool:
        """True when live K8s reads are possible."""
        if self._batch_api is not None:
            return True
        return _in_cluster_configured()

    @property
    def namespace(self) -> str:
        return self._namespace

    def list_active_native_jobs(
        self,
        *,
        destination_id: str | None = None,
    ) -> tuple[NativeJobState, ...] | None:
        """List currently active cryo native-engine Jobs.

        Returns ``None`` when the kubernetes client is not available (signals the
        caller to fall back). Returns an empty tuple when K8s is reachable but no
        matching Jobs exist.
        """
        batch_api = self._batch_api
        if batch_api is None:
            client_mod = _load_k8s_client()
            if client_mod is None or not _in_cluster_configured():
                return None
            batch_api = client_mod.BatchV1Api()
            self._batch_api = batch_api

        label_selector = _NATIVE_JOB_LABEL
        try:
            jobs = batch_api.list_namespaced_job(
                namespace=self._namespace,
                label_selector=label_selector,
            )
        except Exception as exc:
            logger.warning("Failed to list native Jobs in namespace %s: %s", self._namespace, exc)
            return None

        results: list[NativeJobState] = []
        for job in getattr(jobs, "items", []) or []:
            status = getattr(job, "status", None)
            active = int(getattr(status, "active", 0) or 0) if status is not None else 0
            succeeded = int(getattr(status, "succeeded", 0) or 0) if status is not None else 0
            failed = int(getattr(status, "failed", 0) or 0) if status is not None else 0
            if active <= 0 or succeeded > 0 or failed > 0:
                # Active count > 0 is the only state that consumes capacity now;
                # finished jobs (succeeded/failed) are cleaned up by ttlSecondsAfterFinished.
                continue

            metadata = getattr(job, "metadata", None)
            labels = getattr(metadata, "labels", None) or {}
            job_destination = labels.get(_DESTINATION_LABEL)
            if destination_id and job_destination and job_destination != destination_id:
                continue

            table_ref = labels.get("cryo/table_ref")
            cpu_milli, mem_mib = _extract_job_resources(job)

            results.append(
                NativeJobState(
                    name=str(getattr(metadata, "name", "")),
                    namespace=str(getattr(metadata, "namespace", self._namespace)),
                    destination_id=str(job_destination) if job_destination else None,
                    table_ref=str(table_ref) if table_ref else None,
                    cpu_milli=cpu_milli,
                    mem_mib=mem_mib,
                    active_pods=active,
                )
            )

        return tuple(results)


def summarize_native_jobs(jobs: tuple[NativeJobState, ...] | None) -> NativeCapacityTotals:
    """Aggregate resource totals across a set of active Jobs."""
    if not jobs:
        return NativeCapacityTotals(job_count=0, cpu_used_milli=0, mem_used_mib=0)
    return NativeCapacityTotals(
        job_count=len(jobs),
        cpu_used_milli=sum(job.cpu_milli for job in jobs),
        mem_used_mib=sum(job.mem_mib for job in jobs),
    )


@dataclass(frozen=True)
class NativeCapacityTotals:
    """Aggregate of CPU/memory currently consumed by active native Jobs."""

    job_count: int
    cpu_used_milli: int
    mem_used_mib: int
