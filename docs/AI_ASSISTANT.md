# Ask Cryo

Ask Cryo answers questions about the catalogs Cryo already scanned. It can propose a
maintenance or bookkeeping action. It cannot run that action by itself.

## Read and action

Reads use stored scan stats, health grades, and query activity. They do not scan object
storage and they do not issue SQL against a table. "Show me 10 rows" is unsupported.

An action is a proposal. The assistant stores what it would do, who it would touch, and
when the proposal expires (10 minutes by default). Nothing is written until a person
authorizes it.

## Approval

Authorization is a click on the proposal card, or an authenticated approval link opened
by someone with the required role. Typing "yes", a Slack emoji, or a model argument
such as `confirm=true` is not authorization. Chat cannot approve a write.

Writes stay off until both of these are set:

- `CRYO_ASSISTANT_ENABLED=true`
- `CRYO_ASSISTANT_WRITES_ENABLED=true`

Maintenance proposals also require `CRYO_MAINTENANCE_WRITES_ENABLED=true`. With no
assistant settings row, only tier 0 is enabled, so tier 1 and tier 2 proposals fail
closed. Enable tiers on the assistant settings row before operators can approve them.

## What can be proposed

Tier 1 (operator, or viewer where noted): scan a table, save an explorer view (viewer),
subscribe to alerts (viewer), protect or unprotect a snapshot, dismiss an unused-table
candidate (reason required).

Tier 2 (operator): run maintenance, act on a layout recommendation or an approval,
pause or resume maintenance (note required), cancel a run (note required), set a table
parameter override (note required), update a preset.

Tier 3 is not available. There is no purge, force, deactivate, restore, or force-admit
tool. The assistant cannot drop a table or delete files by asking.

Run maintenance goes through the same entry the API uses. A proposal may narrow the
preset's operations. It cannot add operations the preset does not already contain.

## MCP and Slack

`CRYO_ASSISTANT_MCP_ENABLED=true` exposes a JSON-RPC endpoint at `POST /mcp` for protocol
`2026-07-28`. Callers authenticate with a bearer API key that has the `read` scope.
Proposing an action also requires `maintenance:propose`. The response is a proposal id
and an approval path, not a run id. `confirm=true` does not execute.

Slack interactive requests are signature-checked and do not run maintenance. A diagnose
action returns a link to the Cryo run. It does not call a model.

Set `CRYO_SLACK_SIGNING_SECRET` to the Slack signing secret. Requests older than five
minutes are rejected. If the secret is unset, the interaction route rejects the request.

## Limits

- No general SQL, and no ad-hoc row samples.
- Snapshot ids above 2^53 must stay strings. Do not paste them through a JavaScript number.
- Ambiguous table names are not guessed.
- Explaining a cleanup does not perform it. Stopping an answer is not cancelling a run.
- Query activity may live only in table query activity, not in the raw query log.
- Proposals expire. A changed table or preset supersedes the old proposal instead of
  running the stale plan.
- A failed dispatch after a possible commit is marked uncertain and reconciled from the
  run row. The assistant cannot mark a run succeeded by saying so.
