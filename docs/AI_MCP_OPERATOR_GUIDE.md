# Cryo MCP Operator Guide

This guide documents the safe-default posture for Cryo's MCP tool surface.

## Default Safety Posture

- `run_preset` is **disabled by default**.
- Read tools remain available to viewer/operator/admin depending on endpoint auth.
- Every MCP tool invocation writes an `audit_log` row (`action=mcp.<tool_name>`) with actor identity.

## Role Mapping

- Viewer-level tools:
  - `get_table_health`
  - `list_stale_tables`
  - `list_compaction_backlog`
  - `explain_health_change`
  - `simulate_preset`
  - `filter_translation`
  - `get_table_schema`
  - `get_snapshot_history`
- Operator-only tool:
  - `run_preset` (also requires `confirm=true`)

## Enabling Write Tooling

To enable `run_preset`, set:

```bash
CRYO_MCP_RUN_PRESET_ENABLED=true
```

Keep this disabled for production unless your org has approved write-capable agent actions and runbooks are in place.

## Recommended Guardrails

1. Keep API credentials scoped to least privilege.
2. Use short-lived tokens for agent sessions.
3. Review `audit_log` entries for `mcp.*` actions daily.
4. Require explicit human confirmation before write actions from any agent workflow.
