"""Page-aware starter questions drawn from the verified library."""

from __future__ import annotations

from cryo.ai.tools.verified import VERIFIED_HANDLERS

_FLEET = ("worst_tables", "compaction_backlog", "lake_health", "tables_not_updated")
_TABLE = ("table_metadata", "table_health", "why_skipped", "table_partition", "file_layout")
_RUN = ("run_failure", "queue_wait", "diagnose_run", "operation_evidence")
_COST = ("cost_measured", "cost_modeled", "maintenance_impact", "query_activity")
_QUALITY = ("quality_results", "lineage_gap", "schema_summary")
_HELP = ("help_orphan", "help_cooldown")


def starters_for(
    route: str | None,
    table_id: str | None = None,
    run_id: str | None = None,
) -> list[dict[str, str]]:
    path = route or ""
    chosen: tuple[str, ...]
    if run_id or path.startswith("/runs"):
        chosen = _RUN
    elif table_id or path.startswith("/tables"):
        chosen = _TABLE
    elif "cost" in path:
        chosen = _COST
    elif "quality" in path:
        chosen = _QUALITY
    elif "help" in path or path.startswith("/docs"):
        chosen = _HELP
    else:
        chosen = _FLEET
    starters = []
    for handler_id in chosen:
        entry = VERIFIED_HANDLERS.get(handler_id)
        if entry is None:
            continue
        starters.append({"text": str(entry["prompt"]), "verified_handler": handler_id})
    return starters
