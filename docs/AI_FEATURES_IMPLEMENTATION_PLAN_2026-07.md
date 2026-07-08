# AI Features Implementation Plan (2026-07)

> Source of truth: `docs/AI_FEATURES_REVIEW_2026-07.md`
>
> Goal: implement only "genuine AI" features that are grounded, bounded, auditable, failsafe,
> and cheaper than deterministic alternatives where possible.

## Objective

Ship the highest-value AI capabilities for operators without compromising Cryo's trust model:

1. Reliable run-failure diagnosis
2. Natural language to filter-spec translation
3. Explain-this-plan narration in PlanPreview
4. Skillset-driven evaluation and safe MCP tooling
5. Deterministic fallback paths for air-gapped/self-hosted installs

## Open Questions (resolve before implementation starts)

1. Which LLM provider(s) are in-scope for production first: Anthropic, OpenAI, Azure, or local OpenAI-compatible only?
2. Do we require org-level approval before enabling write-capable MCP tools, even when guarded by confirmation?
3. Should failure-diagnosis outcomes be mandatory feedback (thumb required) or optional?
4. Where should token/cost budgets be configured: global settings only, or per-provider + per-feature?
5. For `Skillsets`, should promotion rights be admin-only, or admin + operator with approval?

## Delivery Principles

- No feature executes writes from LLM output without explicit confirmation.
- No provider configured must degrade gracefully (hide or deterministic fallback).
- Every LLM-backed path must emit versioned telemetry and outcome stats.
- Preserve current deterministic SQL rendering and planner behavior.
- Prioritize trust fixes over new AI surfaces if conflicts emerge.

## Phase Plan

### Phase 0 - Platform Prerequisites (blocking)

#### P0.1 Finish Skillsets Lifecycle UI
- Expand `web/src/pages/Skillsets.tsx` from read-only to full lifecycle controls:
  - activate version
  - rollback version
  - promote candidate
  - show outcome metrics and confidence
- Wire existing unused client calls from `web/src/api/client.ts`.
- Role-gate write actions to admin.

#### P0.2 LLM Observation Logging
- Add durable observation writes for every LLM call:
  - skillset id/version
  - prompt hash
  - model id
  - latency
  - token counts
  - outcome id/status
- Ensure this is queryable for scorecards and promotion decisions.

#### P0.3 Configurable Model Defaults
- Replace hardcoded default model constants in `cryo/ai/llm.py` with settings-backed defaults.
- Add validation and sane fallback behavior when configured model is unavailable.

#### P0.4 Token/Cost Budget Guardrails
- Add per-provider budget controls:
  - per-call max tokens
  - daily budget cap
  - reject/abstain mode when over budget
- Expose Prometheus counters for usage and budget denials.

#### P0.5 Local/Air-Gapped Provider Path
- Add OpenAI-compatible base URL override for local model backends (vLLM/Ollama style).
- Ensure all LLM-backed UI features hide or degrade cleanly when provider is absent.

#### P0.6 Wire QPP into Governor Admission
- Add first production call site for `evaluate_qpp_for_job` in governor admission flow.
- Add logs/metrics showing when QPP changed or confirmed admission outcomes.

### Phase 1 - Run Failure Diagnosis (highest-value LLM feature)

#### P1.1 Failure Taxonomy + Prompt Contract
- Define strict taxonomy:
  - commit_conflict
  - engine_oom
  - catalog_timeout_or_ratelimit
  - permission
  - sql_or_dialect
  - storage_error
  - unknown
- Prompt must return structured JSON with:
  - class
  - confidence
  - evidence lines (verbatim references)
  - runbook step

#### P1.2 Diagnosis API + Skillset Binding
- Add backend endpoint that:
  - collects run/job/audit evidence
  - calls skillset-scoped diagnosis function
  - persists outcome-ready records
- Return abstain-safe result (`unknown`) on low confidence.

#### P1.3 UI Integration on Runs Surface
- Add "Diagnose" card/action on failed jobs in:
  - run detail
  - failed table panel
- Show evidence-first output; runbook links must be explicit.

#### P1.4 Feedback Loop
- Add thumbs up/down outcome capture.
- Wire to skillset outcome records for promotion gating.

### Phase 2 - NL -> Filter Spec Translation

#### P2.1 Translation Endpoint
- Build translator that maps NL requests to existing filter-spec schema only.
- Validate every field/operator against known schema and grammar before returning.

#### P2.2 Table Explorer UI
- Add "Find tables like..." input in tables explorer.
- Render translated output as standard filter chips before execution.
- Invalid translations render actionable chip errors.

#### P2.3 Fallback Behavior
- With no provider:
  - hide translator input, or
  - route to deterministic verified-question helper if available.

### Phase 3 - Explain This Plan in PlanPreview

#### P3.1 Deterministic Explanation Layer
- Extend current PlanPreview summary with deterministic plan explanation:
  - operation order
  - guardrail decisions
  - chunking/cooldown/off-peak rationale
  - verifier result framing

#### P3.2 Optional LLM Narration for SQL
- Add bounded narration for rendered SQL intent only.
- Keep SQL and verifier output primary; narration secondary.
- Include deterministic fallback paragraph when provider unavailable.

### Phase 4 - MCP Maturation

#### P4.1 Per-tool RBAC + Audit
- Enforce role checks per MCP tool using existing auth model.
- Emit audit rows for each tool invocation (read and write).

#### P4.2 Expose Safe New Tools
- Add/verify tools:
  - simulate_preset
  - explain_health_change
  - filter-translation as read query helper
- Keep `run_preset` off by default behind explicit capability flag.

#### P4.3 Operator Docs
- Add secure setup docs with minimal example config for agent clients.
- Document write-gating and default-safe posture.

### Phase 5 - Anomaly Root Cause Brief

#### P5.1 Correlation Pipeline
- Build deterministic joins for:
  - schema changes
  - snapshot metadata
  - writer identity
  - commit velocity
  - sibling-table comparison

#### P5.2 Ranked Hypothesis Composer
- LLM composes ranked hypotheses only from pre-joined evidence facts.
- No evidence = no hypothesis.

#### P5.3 UI + Feedback
- Add root-cause brief panel to anomaly surfaces with evidence links.
- Capture thumbs feedback to skillset outcomes.

### Phase 6 - Resource Prediction (classic ML)

#### P6.1 Feature Extraction + Labels
- Build dataset from planner/job telemetry.
- Features include size/files/delete ratio/partition count/runtime context.

#### P6.2 Quantile Regression Predictor
- Implement lightweight quantile predictor aligned with current QPP style.
- Output recommended CPU/memory bounds for native job launches.

#### P6.3 Safe Admission Use
- Start in shadow mode and compare against static sizing.
- Gate enforcement behind confidence and error thresholds.

### Phase 7 - Narrated Digest (polish track)

#### P7.1 Digest Narration Block
- Add 2-3 sentence narrative lead to email/Slack digest.
- Numbers and key facts remain deterministic, verbatim.

#### P7.2 Fallback
- If no provider, render deterministic summary only.

## Testing & Validation Matrix

1. Unit tests for taxonomy parsing, translation validation, fallback paths.
2. Golden tests for NL -> filter-spec conversion outputs.
3. Integration tests for diagnosis API on representative error payloads.
4. UI tests for:
   - no-provider mode
   - invalid translation chips
   - diagnose evidence rendering
5. Policy tests to ensure no write action occurs from LLM output without confirmation.
6. Metrics verification for token usage, budget denials, and skillset outcomes.

## Rollout Strategy

1. Start every new AI feature in shadow/preview mode.
2. Enable per-feature flag:
   - diagnosis
   - translator
   - plan narration
   - anomaly brief
3. Promote skillset versions only on measured acceptance/correction outcomes.
4. Keep deterministic surfaces as first-class fallback indefinitely.

## Suggested Execution Order

1. P0 prerequisites (mandatory)
2. P1 run-failure diagnosis
3. P2 NL -> filter translation
4. P3 explain-this-plan
5. P4 MCP maturation
6. P5 anomaly root-cause brief
7. P6 resource prediction
8. P7 narrated digest

## Definition of Done

- Every LLM-backed feature has:
  - deterministic fallback
  - skillset versioning
  - outcome capture
  - budget enforcement
  - explicit write confirmation
- QPP is actually invoked in governor admission.
- Skillsets UI supports activation/promotion/rollback with visible outcome metrics.
- Operator-facing trust regressions are absent on no-provider deployments.
