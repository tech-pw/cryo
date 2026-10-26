"""The keys a Spark-on-Kubernetes destination reads from ``config.spark_k8s`` (issue #160).

``SparkK8sConfig.from_config`` reads a fixed list of keys and silently ignores everything
else, so a typo or a sizing key placed at the top level of ``config`` ran every job on
chart defaults with no warning. Destination create/patch validates against this list.
"""

from __future__ import annotations

import difflib
from collections.abc import Mapping
from typing import Any

# Read by SparkK8sConfig.from_config.
SPARK_K8S_CONFIG_KEYS = frozenset(
    {
        "namespace",
        "image",
        "image_pull_policy",
        "image_pull_secrets",
        "service_account",
        "catalog_uri",
        "catalog_warehouse",
        "catalog_impl",
        "s3_endpoint",
        "s3_access_key",
        "s3_secret_key",
        "driver_cpu_milli",
        "driver_mem_mib",
        "executor_cpu_milli",
        "executor_mem_mib",
        "executor_instances",
        "node_selector",
        "active_deadline_seconds",
        "ttl_seconds_after_finished",
    }
)
# Read elsewhere from the same block (SparkK8sEngine.with_table_stats).
SPARK_K8S_EXTRA_KEYS = frozenset({"dynamic_sizing"})
SPARK_K8S_KEYS = SPARK_K8S_CONFIG_KEYS | SPARK_K8S_EXTRA_KEYS

# Only meaningful inside spark_k8s. catalog_uri / catalog_warehouse / catalog_impl are also
# read from the top level on purpose (issue #149), so they are not in this set.
_NESTED_ONLY_KEYS = frozenset(
    {
        "driver_cpu_milli",
        "driver_mem_mib",
        "executor_cpu_milli",
        "executor_mem_mib",
        "executor_instances",
        "node_selector",
        "active_deadline_seconds",
        "ttl_seconds_after_finished",
        "dynamic_sizing",
        "image_pull_policy",
        "image_pull_secrets",
        "service_account",
    }
)
_POSITIVE_INT_KEYS = frozenset(
    {
        "driver_cpu_milli",
        "driver_mem_mib",
        "executor_cpu_milli",
        "executor_mem_mib",
        "executor_instances",
        "active_deadline_seconds",
        "ttl_seconds_after_finished",
    }
)


def is_spark_k8s_destination(engine: object, config: Mapping[str, Any]) -> bool:
    value = getattr(engine, "value", engine)
    if str(value).lower() not in {"spark_emr", "spark"}:
        return False
    return str(config.get("submit_mode") or "").strip().lower() == "k8s"


def _suggest(key: str) -> str:
    match = difflib.get_close_matches(key, sorted(SPARK_K8S_KEYS), n=1, cutoff=0.6)
    return f" Did you mean '{match[0]}'?" if match else ""


def validate_spark_k8s_config(config: Mapping[str, Any]) -> None:
    """Raise ValueError naming the first misplaced, unknown or malformed spark_k8s key."""
    misplaced = sorted(key for key in config if key in _NESTED_ONLY_KEYS)
    if misplaced:
        names = ", ".join(f"config.{key}" for key in misplaced)
        raise ValueError(
            f"{names} {'is' if len(misplaced) == 1 else 'are'} ignored at the top level of a "
            "Spark-on-Kubernetes destination; move "
            f"{'it' if len(misplaced) == 1 else 'them'} under config.spark_k8s."
        )
    block = config.get("spark_k8s")
    if block is None:
        return
    if not isinstance(block, Mapping):
        raise ValueError("config.spark_k8s must be an object")
    for key in sorted(block):
        if key not in SPARK_K8S_KEYS:
            raise ValueError(
                f"config.spark_k8s.{key} is not a Spark-on-Kubernetes setting.{_suggest(key)}"
            )
    for key in sorted(_POSITIVE_INT_KEYS & set(block)):
        value = block[key]
        if value is None:
            continue
        if isinstance(value, bool):
            raise ValueError(f"config.spark_k8s.{key} must be a positive integer")
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"config.spark_k8s.{key} must be a positive integer") from None
        if number <= 0 or (isinstance(value, float) and not value.is_integer()):
            raise ValueError(f"config.spark_k8s.{key} must be a positive integer")
    dynamic = block.get("dynamic_sizing")
    if dynamic is not None and not isinstance(dynamic, bool):
        raise ValueError("config.spark_k8s.dynamic_sizing must be true or false")
