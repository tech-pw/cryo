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


class _Masked:
    """Attribute proxy that answers selected names without touching the wrapped ORM object,
    so `from_attributes` validation can't lazy-load a deferred column."""

    def __init__(self, obj: object, **overrides: object) -> None:
        self._obj = obj
        self._overrides = overrides

    def __getattr__(self, name: str) -> object:
        if name in self._overrides:
            return self._overrides[name]
        return getattr(self._obj, name)


def table_to_read(table: Table, *, include_raw: bool = True) -> TableRead:
    """Serialize a table. List endpoints pass ``include_raw=False``: `stats.raw` carries
    the scanner's per-partition detail (up to MBs per table), which only detail views read."""
    if include_raw or table.stats is None:
        read = TableRead.model_validate(table)
    else:
        read = TableRead.model_validate(_Masked(table, stats=_Masked(table.stats, raw=None)))
    updates: dict[str, object] = {}
    if table.last_scan_error:
        category = classify_scan_failure(table.last_scan_error)
        updates["failure_category"] = category
        updates["failure_category_label"] = failure_category_label(category)
        updates["failure_category_guidance"] = failure_category_guidance(
            category, table.last_scan_error
        )
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
