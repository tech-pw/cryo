"""Kubernetes Job dispatch for per-chunk ad-hoc scans.

Reuses cryo.engines.native.job_dispatch's Job-building machinery (manifest
shape, payload Secret + ownerRef, spot/on-demand scheduling annotations) with
a scan-specific container entrypoint instead of the native engine binary --
same image/ServiceAccount/RBAC the native engine already uses (see
deploy/helm/cryo/templates/governor-rbac.yaml), gated by scanner.jobDispatch
in addition to native.enabled.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from cryo.engines.native.job_dispatch import JobCreateResult, create_native_job

_SCAN_COMMAND = ["python", "-m", "cryo.scanner.job_runner"]


def create_scan_job(
    payload: Mapping[str, Any],
    *,
    config: Mapping[str, Any],
    cpu_milli: int,
    mem_mib: int,
    job_name: str | None = None,
    run_id: str | None = None,
    table_refs: Sequence[str] | None = None,
    timeout_seconds: int | None = None,
) -> JobCreateResult:
    """Dispatch one scan chunk as a Kubernetes Job.

    ``payload`` is the already-claimed ScanQueue item (same shape
    ScanWorker.process_next() consumes) -- the Job's container decodes it
    from the payload Secret and runs it directly via cryo.scanner.job_runner,
    it does not re-claim from the queue.
    """
    return create_native_job(
        payload,
        config={**dict(config), "k8s_batch_entrypoint": False},
        cpu_milli=cpu_milli,
        mem_mib=mem_mib,
        job_name=job_name or _scan_job_name(run_id),
        run_id=run_id,
        table_refs=table_refs,
        timeout_seconds=timeout_seconds,
        command=_SCAN_COMMAND,
    )


def _scan_job_name(run_id: str | None) -> str:
    suffix = uuid.uuid4().hex[:8]
    if run_id:
        return f"cryo-scan-{str(run_id)[:8]}-{suffix}"
    return f"cryo-scan-{suffix}"
