"""Read-only MCP-style tool surface over Cryo metadata."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class McpTool:
    name: str
    description: str
    handler: ToolHandler


class CryoMcpServer:
    """MCP-style tool surface over Cryo metadata.

    **EXPERIMENTAL** — this module is shipped as a thin in-process API, not as a standalone
    MCP server. It is not on the v1 launch surface (see ``docs/OSS_READINESS.md`` §2.2) and is
    not exercised by the production deployment. Do not point an LLM agent at the tool
    registry assuming it is read-only: ``run_preset`` is a **write path** that triggers a
    real maintenance job and must only be invoked with an operator-scoped credential after
    explicit confirmation. All other registered tools are read-only.

    The tool descriptions in ``_TOOL_DESCRIPTIONS`` are the canonical source of truth for
    what each tool does; this module-level note exists so any future code reader sees the
    warning alongside the class definition.
    """

    def __init__(self) -> None:
        self._tools: dict[str, McpTool] = {}

    def register(self, tool: McpTool) -> None:
        self._tools[tool.name] = tool

    def list_tools(self) -> list[dict[str, str]]:
        return [
            {"name": tool.name, "description": tool.description} for tool in self._tools.values()
        ]

    def call(self, name: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"Unknown tool: {name}")
        return tool.handler(dict(arguments or {}))


def build_default_mcp_server(
    *,
    get_table_health: ToolHandler | None = None,
    list_stale_tables: ToolHandler | None = None,
    list_compaction_backlog: ToolHandler | None = None,
    explain_health_change: ToolHandler | None = None,
    simulate_preset: ToolHandler | None = None,
    get_table_schema: ToolHandler | None = None,
    get_snapshot_history: ToolHandler | None = None,
    run_preset: ToolHandler | None = None,
) -> CryoMcpServer:
    server = CryoMcpServer()
    handlers = {
        "get_table_health": get_table_health,
        "list_stale_tables": list_stale_tables,
        "list_compaction_backlog": list_compaction_backlog,
        "explain_health_change": explain_health_change,
        "simulate_preset": simulate_preset,
        "get_table_schema": get_table_schema,
        "get_snapshot_history": get_snapshot_history,
        "run_preset": run_preset,
    }
    for name, handler in handlers.items():
        if handler is not None:
            server.register(
                McpTool(
                    name=name,
                    description=_TOOL_DESCRIPTIONS[name],
                    handler=handler,
                )
            )
    return server


_TOOL_DESCRIPTIONS = {
    "get_table_health": "Return health grade, score, and key stats for a table.",
    "list_stale_tables": "List tables not maintained within the configured cooldown window.",
    "list_compaction_backlog": "List tables with high small-file counts ranked by priority.",
    "explain_health_change": "Explain a before/after health diff in plain English.",
    "simulate_preset": "Simulate a preset against current table inventory.",
    "get_table_schema": "Return partition fields and column quality stats from the latest scan.",
    "get_snapshot_history": "Return snapshot count trajectory from table stats history.",
    "run_preset": "Trigger a preset run (write path; requires confirm=true and operator scope).",
}
