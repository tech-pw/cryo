from __future__ import annotations

from cryo.api.schemas import TableRead
from cryo.db.model_utils import catalog_name_for_table, qualified_table_name
from cryo.db.models import Table
from cryo.domain.enums import CostPrecision
from cryo.scanner.failure_classifier import (
    classify_scan_failure,
    failure_category_actionable_from_cryo,
    failure_category_guidance,
    failure_category_label,
)


def table_to_read(table: Table) -> TableRead:
    read = TableRead.model_validate(table)
    updates: dict[str, object] = {}
    if table.last_scan_error:
        category = classify_scan_failure(table.last_scan_error)
        updates["failure_category"] = category
        updates["failure_category_label"] = failure_category_label(category)
        updates["failure_category_guidance"] = failure_category_guidance(category)
        updates["failure_category_actionable_from_cryo"] = failure_category_actionable_from_cryo(
            category
        )
    if table.source is not None:
        updates["catalog_name"] = catalog_name_for_table(table)
        updates["source_name"] = table.source.name
        updates["qualified_table_name"] = qualified_table_name(table)
    if read.stats is not None and table.cost is not None:
        precision = table.cost.precision
        try:
            cost_precision = CostPrecision(precision)
        except ValueError:
            cost_precision = None
        updates["stats"] = read.stats.model_copy(
            update={
                "cost_precision": cost_precision,
                "cost_last_full_scan_at": table.cost.last_full_cost_scan_at,
            }
        )
    if not updates:
        return read
    return read.model_copy(update=updates)
