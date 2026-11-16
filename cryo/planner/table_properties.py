"""Plan for a table-property change job (#560).

A property change on a table PyIceberg cannot commit to (Iceberg v3) runs as engine DDL, as
a job like any other engine work: per-table lock, governor admission, audit. The planner
only turns the request carried in the job params into operations; it emits no SQL and never
adds maintenance to the job.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cryo.domain.enums import Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation

# Job params key the API writes the requested change under (run summary -> job params).
TABLE_PROPERTIES_PARAM = "table_properties"


class TablePropertiesPlanError(ValueError):
    pass


def is_property_job(requested: object) -> bool:
    """True when a job's explicit request is a property change."""
    if not isinstance(requested, (list, tuple)):
        return False
    return OperationType.SET_TABLE_PROPERTIES.value in {str(op) for op in requested}


def property_change(params: Mapping[str, Any]) -> tuple[dict[str, str], list[str]]:
    raw = params.get(TABLE_PROPERTIES_PARAM)
    if not isinstance(raw, Mapping):
        raise TablePropertiesPlanError("property change job has no table_properties params")
    set_raw = raw.get("set") or {}
    remove_raw = raw.get("remove") or []
    if not isinstance(set_raw, Mapping) or not isinstance(remove_raw, (list, tuple)):
        raise TablePropertiesPlanError("table_properties must hold a set map and a remove list")
    sets = {str(key): str(value) for key, value in set_raw.items()}
    removes = sorted({str(key) for key in remove_raw} - set(sets))
    if not sets and not removes:
        raise TablePropertiesPlanError("property change job has nothing to set or remove")
    return sets, removes


def build_property_plan(
    table_ref: str,
    mode: Mode | str,
    params: Mapping[str, Any],
) -> MaintenancePlan:
    """One operation for the keys to set, one for the keys to remove. Nothing else."""
    sets, removes = property_change(params)
    operations: list[Operation] = []
    if sets:
        operations.append(Operation(OperationType.SET_TABLE_PROPERTIES, {"set": sets}))
    if removes:
        operations.append(Operation(OperationType.SET_TABLE_PROPERTIES, {"remove": removes}))
    keys = ", ".join([*sorted(sets), *removes])
    return MaintenancePlan(
        table_ref=table_ref,
        mode=Mode(mode),
        operations=tuple(operations),
        notes=(f"Planned a table property change: {keys}",),
    )
