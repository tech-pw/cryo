"""Security contexts for the Kubernetes Jobs Cryo creates at runtime.

These mirror the Helm chart's ``containerSecurityContext`` for pods the chart never
renders (native engine, Spark submit, maintenance lanes, analysis). UID is left to the
image's own non-root ``USER`` so a separately pinned engine image keeps working.
"""

from __future__ import annotations

from typing import Any


def pod_security_context() -> dict[str, Any]:
    return {"seccompProfile": {"type": "RuntimeDefault"}}


def container_security_context() -> dict[str, Any]:
    return {
        "allowPrivilegeEscalation": False,
        "privileged": False,
        "capabilities": {"drop": ["ALL"]},
    }
