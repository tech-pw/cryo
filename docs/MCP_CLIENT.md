# MCP client

`POST /mcp` is the streamable HTTP adapter for protocol `2026-07-28`. The route is not registered unless `CRYO_ASSISTANT_MCP_ENABLED=true`. That setting defaults to false.

Send `Authorization: Bearer <api key>` and `mcp-protocol-version: 2026-07-28`. The key needs the `read` scope. Proposing an action also needs `maintenance:propose`. An unknown or revoked key is rejected. `confirm=true` does not run an action.

Tested client: conformance tests only; no live client was run.
