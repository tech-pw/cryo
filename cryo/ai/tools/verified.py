"""Keyword-matched verified questions. No embeddings. Unresolved table names do not guess."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from cryo.ai.tools.evidence import EvidenceEnvelope
from cryo.api.security import Principal, Role

VerifiedHandler = Callable[[Mapping[str, Any], Session], dict[str, Any]]

_QUALIFIED = re.compile(r"\b([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)\b")
_OF_NAME = re.compile(r"\b(?:of|for)\s+([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)?)\b", re.IGNORECASE)
_UUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)

# Handlers that cannot answer because the Iceberg snapshot catalog is not a stored table.
ABSTAIN_MISSING_SOURCE: dict[str, str] = {
    "snapshot_list": "iceberg snapshot catalog",
    "compare_snapshots": "iceberg snapshot catalog",
}


def _table_from(question: str, context: Mapping[str, Any]) -> dict[str, str] | None:
    if context.get("table_id"):
        return {"table_id": str(context["table_id"])}
    schema = context.get("schema_name")
    name = context.get("table_name")
    if schema and name:
        return {"schema_name": str(schema), "table_name": str(name)}
    qualified = context.get("qualified_name") or context.get("table")
    if isinstance(qualified, str) and "." in qualified:
        schema_name, table_name = qualified.split(".", 1)
        return {"schema_name": schema_name, "table_name": table_name}
    match = _QUALIFIED.search(question)
    if match:
        return {"schema_name": match.group(1), "table_name": match.group(2)}
    named = _OF_NAME.search(question)
    if named:
        token = named.group(1)
        if "." in token:
            schema_name, table_name = token.split(".", 1)
            return {"schema_name": schema_name, "table_name": table_name}
        return {"table_name": token}
    if isinstance(name, str) and name:
        return {"table_name": name}
    return None


def _two_tables(question: str, context: Mapping[str, Any]) -> dict[str, str] | None:
    if context.get("left_table_id") and context.get("right_table_id"):
        return {
            "left_table_id": str(context["left_table_id"]),
            "right_table_id": str(context["right_table_id"]),
        }
    found = _QUALIFIED.findall(question)
    if len(found) < 2:
        return None
    return {
        "left_schema": found[0][0],
        "left_table": found[0][1],
        "right_schema": found[1][0],
        "right_table": found[1][1],
    }


def _run_id(question: str, context: Mapping[str, Any]) -> str | None:
    if context.get("run_id"):
        return str(context["run_id"])
    match = _UUID.search(question)
    return match.group(0) if match else None


def _params(
    needs: str, question: str, context: Mapping[str, Any], feature: str | None
) -> dict[str, Any] | None:
    if needs == "none":
        params: dict[str, Any] = {}
        if feature:
            params["feature"] = feature
            params["query"] = feature
        return params
    if needs == "table":
        return _table_from(question, context)
    if needs == "run":
        run_id = _run_id(question, context)
        if run_id is None:
            return None
        return {"run_id": run_id}
    if needs == "two_tables":
        return _two_tables(question, context)
    if needs == "feature":
        if not feature:
            return None
        return {"feature": feature, "query": feature}
    return None


def _principal(context: Mapping[str, Any]) -> Principal:
    raw = context.get("principal")
    if isinstance(raw, Principal):
        return raw
    return Principal(subject="ask-cryo", roles=frozenset({Role.VIEWER}))


def _run_tool(
    session: Session, context: Mapping[str, Any], tool_name: str, arguments: dict[str, Any]
) -> EvidenceEnvelope:
    from cryo.ai.tools.bootstrap import register_builtin_tools
    from cryo.ai.tools.registry import ToolContext, get_registry
    from cryo.config import Settings

    register_builtin_tools()
    settings = context.get("settings")
    if not isinstance(settings, Settings):
        settings = Settings()
    now = context.get("now")
    if not isinstance(now, datetime):
        now = datetime.now(UTC)
    ctx = ToolContext(
        session=session,
        principal=_principal(context),
        settings=settings,
        now=now,
        timezone=str(context.get("timezone") or "UTC"),
    )
    return get_registry().call(tool_name, ctx, arguments)


def _pack(
    handler_id: str, parameters: dict[str, Any], envelope: EvidenceEnvelope
) -> dict[str, Any]:
    payload = envelope.result
    summary = payload.get("summary")
    if isinstance(summary, str) and summary:
        answer = summary
    elif envelope.warnings:
        answer = envelope.warnings[0]
    else:
        answer = "Stored evidence only."
    packed: dict[str, Any] = {
        "answer": answer,
        "parameters": parameters,
        "result": {
            "evidence_id": envelope.evidence_id,
            "classification": envelope.classification,
            "coverage": envelope.coverage,
            "warnings": envelope.warnings,
            "unsupported_fields": envelope.unsupported_fields,
            "error_category": envelope.error_category,
            "truncated": envelope.truncated,
            "row_count": envelope.row_count,
            "units": envelope.units,
            "measurement_method": envelope.measurement_method,
            "observed_at": envelope.observed_at.isoformat() if envelope.observed_at else None,
            "payload": payload,
        },
        "classification": envelope.classification,
    }
    if handler_id == "worst_tables":
        rows = payload.get("lowest_scores", {})
        if isinstance(rows, dict) and isinstance(rows.get("rows"), list):
            packed["tables"] = rows["rows"]
    if handler_id == "compaction_backlog":
        rows = payload.get("needs_compaction", {})
        if isinstance(rows, dict) and isinstance(rows.get("rows"), list):
            packed["tables"] = rows["rows"]
    if payload.get("abstained") is True:
        packed["result"]["abstained"] = True
        packed["result"]["abstain_reason"] = payload.get("abstain_reason")
    return packed


def _bind(handler_id: str, tool: str, needs: str, feature: str | None) -> VerifiedHandler:
    def handler(context: Mapping[str, Any], session: Session) -> dict[str, Any]:
        question = str(context.get("question") or "")
        parameters = _params(needs, question, context, feature)
        if parameters is None:
            return {
                "answer": "I need one table or run before I can answer. I will not guess.",
                "parameters": {},
                "result": {"clarification": True, "handler": handler_id},
                "classification": "unknown",
            }
        envelope = _run_tool(session, context, tool, parameters)
        return _pack(handler_id, parameters, envelope)

    return handler


_SPECS: tuple[tuple[str, str, tuple[str, ...], str, str, str | None, float], ...] = (
    (
        "worst_tables",
        "Which tables are the unhealthiest right now?",
        (
            r"\bunhealthiest\b",
            r"\bunhealthy\b",
            r"\bworst tables\b",
            r"\blowest scores?\b",
        ),
        "get_lake_health",
        "none",
        None,
        0.9,
    ),
    (
        "compaction_backlog",
        "Which tables have the largest compaction backlog?",
        (r"\bcompaction backlog\b", r"\bcompaction\b", r"\bsmall files\b", r"\bneeds compaction\b"),
        "get_lake_health",
        "none",
        None,
        0.9,
    ),
    (
        "lake_health",
        "What is the fleet health grade mix?",
        (r"\bfleet health\b", r"\blake health\b", r"\bgrade mix\b"),
        "get_lake_health",
        "none",
        None,
        0.88,
    ),
    (
        "grade_distribution",
        "How are table grades distributed?",
        (r"\bgrade distribution\b", r"\bhow many tables are grade\b"),
        "get_lake_health",
        "none",
        None,
        0.86,
    ),
    (
        "never_scanned",
        "Which tables were never scanned?",
        (r"\bnever scanned\b", r"\bno scan\b"),
        "list_tables_by_commit_time",
        "none",
        None,
        0.88,
    ),
    (
        "scan_failures",
        "Which tables failed their last scan?",
        (r"\bscan failed\b", r"\bscan failures?\b"),
        "list_tables_by_commit_time",
        "none",
        None,
        0.88,
    ),
    (
        "catalog_health",
        "Is the catalog healthy?",
        (r"\bcatalog health\b", r"\bsource health\b"),
        "get_lake_health",
        "none",
        None,
        0.86,
    ),
    (
        "table_metadata",
        "What is this table's partition, location, owner, and last commit?",
        (r"\btable metadata\b", r"\bpartition, location\b", r"\bowner and location\b"),
        "get_table_metadata",
        "table",
        None,
        0.9,
    ),
    (
        "table_partition",
        "What partition transform does this table use?",
        (r"\bpartition transform\b", r"\bpartition column\b", r"\bhow is .+ partitioned\b"),
        "get_table_metadata",
        "table",
        None,
        0.88,
    ),
    (
        "table_location",
        "Where is this table stored?",
        (r"\btable location\b", r"\blocation of\b", r"\bs3 location\b", r"\bwhere is .+ stored\b"),
        "get_table_metadata",
        "table",
        None,
        0.88,
    ),
    (
        "table_owner",
        "Who owns this table?",
        (r"\bwho owns\b", r"\btable owner\b", r"\bowner email\b"),
        "get_table_metadata",
        "table",
        None,
        0.88,
    ),
    (
        "table_last_commit",
        "When was this table last committed, as of the last scan?",
        (r"\blast commit\b", r"\blast snapshot commit\b", r"\bwhen was .+ committed\b"),
        "get_table_metadata",
        "table",
        None,
        0.88,
    ),
    (
        "table_health",
        "What is this table's health grade?",
        (r"\bhealth of\b", r"\bgrade for\b", r"\bhow healthy is\b", r"\btable health\b"),
        "get_table_health",
        "table",
        None,
        0.88,
    ),
    (
        "compare_health",
        "Compare the health of two tables.",
        (r"\bcompare health\b", r"\bhealth compared\b"),
        "compare_health",
        "two_tables",
        None,
        0.9,
    ),
    (
        "file_layout",
        "What is the stored file layout?",
        (r"\bfile layout\b", r"\bfile sizes\b", r"\bsmall file count for\b"),
        "get_file_layout",
        "table",
        None,
        0.86,
    ),
    (
        "partition_candidates",
        "Which partitions are compaction candidates?",
        (r"\bpartition candidates\b", r"\bwhich partitions\b"),
        "list_partition_candidates",
        "table",
        None,
        0.86,
    ),
    (
        "schema_summary",
        "What columns does the scan index show?",
        (r"\bschema of\b", r"\bcolumns of\b", r"\bwhat columns\b"),
        "get_schema",
        "table",
        None,
        0.86,
    ),
    (
        "tables_not_updated",
        "Which tables have not been updated as of the last scan?",
        (r"\bnot updated\b", r"\bstale commit\b", r"\bhaven't been updated\b"),
        "list_tables_by_commit_time",
        "none",
        None,
        0.88,
    ),
    (
        "snapshot_list",
        "List snapshot ids for this table.",
        (r"\blist snapshots\b", r"\bsnapshot ids?\b"),
        "list_snapshots",
        "table",
        None,
        0.9,
    ),
    (
        "snapshot_refs",
        "What snapshot refs are stored?",
        (r"\bsnapshot refs\b", r"\bprotected snapshots\b", r"\bbranches and tags\b"),
        "get_snapshot_refs",
        "table",
        None,
        0.86,
    ),
    (
        "expiry_preview",
        "Which snapshots would be eligible to expire? Eligible is not deleted.",
        (r"\bexpiry preview\b", r"\beligible to expire\b", r"\bexpire snapshots\b"),
        "preview_expiry",
        "table",
        None,
        0.86,
    ),
    (
        "why_skipped",
        "Why was maintenance skipped?",
        (r"\bwhy (?:was|were|is).{0,40}skip", r"\bskipped\b", r"\bwhy deferred\b"),
        "get_maintenance_decisions",
        "table",
        None,
        0.9,
    ),
    (
        "effective_policy",
        "What preset is pinned for this table?",
        (r"\beffective policy\b", r"\bwhich preset\b", r"\bpinned preset\b"),
        "get_effective_policy",
        "table",
        None,
        0.86,
    ),
    (
        "workflows",
        "Which workflows are scheduled?",
        (r"\blist workflows\b", r"\bscheduled workflows\b", r"\bwhat workflows\b"),
        "list_workflows",
        "none",
        None,
        0.86,
    ),
    (
        "maintenance_decisions",
        "What maintenance decision is recorded?",
        (r"\bmaintenance decision\b", r"\bdecision record\b"),
        "get_maintenance_decisions",
        "table",
        None,
        0.86,
    ),
    (
        "recent_runs",
        "What are the recent maintenance runs?",
        (r"\brecent runs\b", r"\blist runs\b", r"\blatest runs\b"),
        "list_runs",
        "none",
        None,
        0.86,
    ),
    (
        "run_failure",
        "Why did this run fail?",
        (r"\brun failure\b", r"\bwhy did the run fail\b", r"\bfailed run\b"),
        "diagnose_failure",
        "run",
        None,
        0.9,
    ),
    (
        "queue_wait",
        "How many jobs are waiting in the queue?",
        (r"\bqueue wait\b", r"\bhow many queued\b", r"\bwhy is .+ waiting\b"),
        "get_queue_summary",
        "none",
        None,
        0.88,
    ),
    (
        "operation_evidence",
        "What operation evidence is stored for this run?",
        (r"\boperation evidence\b", r"\bwhat operations ran\b"),
        "get_operation_evidence",
        "run",
        None,
        0.86,
    ),
    (
        "diagnose_run",
        "Diagnose this run from stored error lines.",
        (r"\bdiagnose\b", r"\broot cause\b"),
        "diagnose_failure",
        "run",
        None,
        0.84,
    ),
    (
        "admission_state",
        "What is the admission state?",
        (r"\badmission state\b", r"\bare we throttled\b", r"\bengine slots\b"),
        "get_admission_state",
        "none",
        None,
        0.88,
    ),
    (
        "queue_summary",
        "Summarize the maintenance queue.",
        (r"\bqueue summary\b", r"\bqueue depth\b"),
        "get_queue_summary",
        "none",
        None,
        0.86,
    ),
    (
        "engine_status",
        "What engines are configured?",
        (r"\bengine status\b", r"\bwhich engines\b"),
        "get_engine_status",
        "none",
        None,
        0.86,
    ),
    (
        "schedule_status",
        "What maintenance schedule is stored?",
        (r"\bwhat is the schedule\b", r"\bschedule cron\b", r"\bwhen does maintenance run\b"),
        "list_workflows",
        "none",
        None,
        0.86,
    ),
    (
        "cost_measured",
        "What measured cost do we have?",
        (r"\bmeasured cost\b", r"\bactual cost\b", r"\brequest cost\b"),
        "get_cost_evidence",
        "none",
        None,
        0.9,
    ),
    (
        "cost_modeled",
        "What modeled cost estimate do we have?",
        (r"\bmodeled cost\b", r"\bestimated cost\b", r"\bprojected savings\b"),
        "get_cost_evidence",
        "none",
        None,
        0.88,
    ),
    (
        "maintenance_impact",
        "How much space did successful reclaim actions free?",
        (r"\bmaintenance impact\b", r"\bbytes freed\b", r"\bhow much did we reclaim\b"),
        "get_maintenance_impact",
        "none",
        None,
        0.88,
    ),
    (
        "query_activity",
        "What query activity is stored?",
        (r"\bquery activity\b", r"\bwho is reading\b", r"\bquery count\b"),
        "get_query_activity",
        "none",
        None,
        0.86,
    ),
    (
        "quality_results",
        "What quality constraint results are stored?",
        (r"\bquality results\b", r"\bconstraint results\b", r"\bdata quality\b"),
        "get_quality_results",
        "table",
        None,
        0.86,
    ),
    (
        "lineage_gap",
        "What lineage is stored, and where is the gap?",
        (r"\blineage\b", r"\bupstream tables\b", r"\bdownstream tables\b"),
        "get_lineage",
        "table",
        None,
        0.84,
    ),
    (
        "help_orphan",
        "What do the docs say about orphan files?",
        (r"\borphan files?\b", r"\bremove orphan\b", r"\bwhat are orphan\b"),
        "get_feature_help",
        "feature",
        "orphan",
        0.9,
    ),
    (
        "help_cooldown",
        "What do the docs say about cooldown?",
        (r"\bwhat is cooldown\b", r"\bexplain cooldown\b", r"\bcooldown window\b"),
        "get_feature_help",
        "feature",
        "cooldown",
        0.9,
    ),
    (
        "help_optimize",
        "What do the docs say about optimize?",
        (r"\bwhat does optimize\b", r"\bexplain optimize\b"),
        "get_feature_help",
        "feature",
        "optimize",
        0.84,
    ),
    (
        "feature_help",
        "Show feature help from the docs.",
        (r"\bfeature help\b", r"\bhow does cryo\b"),
        "get_feature_help",
        "feature",
        "maintenance",
        0.8,
    ),
    (
        "search_tables",
        "Search stored table names.",
        (r"\bsearch tables\b", r"\bfind table named\b"),
        "search_tables",
        "none",
        None,
        0.86,
    ),
    (
        "capabilities",
        "What can Ask Cryo do?",
        (r"\bwhat can you do\b", r"\bcapabilities\b", r"\bwhich tools\b"),
        "get_capabilities",
        "none",
        None,
        0.86,
    ),
)


def _build() -> dict[str, dict[str, Any]]:
    library: dict[str, dict[str, Any]] = {}
    for handler_id, prompt, patterns, tool, needs, feature, score in _SPECS:
        library[handler_id] = {
            "prompt": prompt,
            "patterns": list(patterns),
            "handler": _bind(handler_id, tool, needs, feature),
            "needs": needs,
            "tool": tool,
            "feature": feature,
            "score": score,
        }
    return library


VERIFIED_HANDLERS: dict[str, dict[str, Any]] = _build()


def match_verified(
    question: str, context: Mapping[str, Any] | None = None
) -> dict[str, Any] | None:
    """Return the best keyword match whose parameters resolve, or None to ask a clarification."""
    ctx = context or {}
    text = question.strip().lower()
    if not text:
        return None
    ranked: list[tuple[float, str]] = []
    for handler_id, entry in VERIFIED_HANDLERS.items():
        prompt = str(entry["prompt"]).lower()
        score = 0.0
        if text == prompt or text == handler_id.replace("_", " "):
            score = 1.0
        else:
            for pattern in entry["patterns"]:
                if re.search(pattern, text, re.IGNORECASE):
                    score = max(score, float(entry["score"]))
        if score >= 0.8:
            ranked.append((score, handler_id))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    for score, handler_id in ranked:
        entry = VERIFIED_HANDLERS[handler_id]
        parameters = _params(str(entry["needs"]), question, ctx, entry.get("feature"))
        if parameters is None:
            continue
        return {"handler": handler_id, "parameters": parameters, "score": score}
    return None


def abstaining_handlers() -> dict[str, str]:
    """Handlers that abstain because a source table does not exist."""
    return dict(ABSTAIN_MISSING_SOURCE)
