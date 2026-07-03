from __future__ import annotations

from cryo.api.schemas import TableRead
from cryo.db.models import Table
from cryo.domain.enums import CostPrecision


def table_to_read(table: Table) -> TableRead:
    read = TableRead.model_validate(table)
    if read.stats is None or table.cost is None:
        return read
    precision = table.cost.precision
    try:
        cost_precision = CostPrecision(precision)
    except ValueError:
        cost_precision = None
    return read.model_copy(
        update={
            "stats": read.stats.model_copy(
                update={
                    "cost_precision": cost_precision,
                    "cost_last_full_scan_at": table.cost.last_full_cost_scan_at,
                }
            )
        }
    )
