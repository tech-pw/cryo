"""Kubernetes pod labels that AWS Split Cost Allocation imports into CUR.

AWS-generated ``aws:eks:workload-name`` / ``aws:eks:deployment`` stay empty for
Job-owned pods (and for Spark driver/executor pods Spark creates with no Job
ownerRef). Split Cost Allocation *does* ingest **pod labels** as user-defined
cost allocation tags after they are activated on the payer account:

https://docs.aws.amazon.com/cur/latest/userguide/split-cost-allocation-data-kubernetes-labels.html

Athena CUR 2.0 columns (after activation, ~24–48h):

- ``resource_tags_user_eks_workload_name``
- ``resource_tags_user_eks_workload_type``
- ``resource_tags_user_cryo_engine``
- ``resource_tags_user_cryo_operation``

Hyphenated keys (no ``/``) keep those column names readable. Keys sort early
enough to stay inside the 50-label-per-pod import cap.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

# CUR / Grafana: closest user-tag analogue of resource_tags_aws_eks_workload_name.
WORKLOAD_NAME_LABEL = "eks-workload-name"
WORKLOAD_TYPE_LABEL = "eks-workload-type"
ENGINE_LABEL = "cryo-engine"
OPERATION_LABEL = "cryo-operation"

_LABEL_VALUE_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def k8s_label_value(raw: str, *, default: str = "unknown") -> str:
    cleaned = _LABEL_VALUE_RE.sub("-", raw.strip().lower()).strip("-_.")
    cleaned = cleaned[:63].strip("-_.")
    return cleaned or default


def cost_attribution_labels(
    *,
    workload_name: str,
    engine: str,
    operation: str,
) -> dict[str, str]:
    return {
        WORKLOAD_NAME_LABEL: k8s_label_value(workload_name, default="cryo"),
        WORKLOAD_TYPE_LABEL: "Job",
        ENGINE_LABEL: k8s_label_value(engine, default="unknown"),
        OPERATION_LABEL: k8s_label_value(operation, default="unknown"),
    }


def operation_from_native_payload(
    payload: Mapping[str, Any],
    *,
    command: Sequence[str] | None = None,
) -> tuple[str, str, str]:
    """Return (workload_name, engine, operation) for a native/scan Job payload."""
    if command is not None and any("scanner.job_runner" in str(part) for part in command):
        return "cryo-scanner", "scanner", "scan"

    types: list[str] = []
    _collect_op_types(payload.get("operations"), types)
    tables = payload.get("tables")
    if isinstance(tables, list):
        for table in tables:
            if isinstance(table, Mapping):
                _collect_op_types(table.get("operations"), types)

    unique = sorted({k8s_label_value(t) for t in types if t})
    if not unique:
        operation = "maintenance"
    elif len(unique) == 1:
        operation = unique[0]
    else:
        joined = "-".join(unique)
        operation = joined if len(joined) <= 63 else "mixed"
    return "cryo-native", "native", operation


def _collect_op_types(raw: Any, out: list[str]) -> None:
    if not isinstance(raw, list):
        return
    for item in raw:
        if isinstance(item, Mapping) and item.get("type"):
            out.append(str(item["type"]))
        elif isinstance(item, str) and item:
            out.append(item)
