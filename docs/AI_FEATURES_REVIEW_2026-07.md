# Genuine AI for Cryo — review and proposal, 2026-07-08

> Scope: what AI capability would make Cryo *measurably more useful to an operator*, versus
> AI added for the badge. Method: audit of the existing AI/ML surface in `cryo/ai/` and
> `cryo/ml/` (verified against the working tree), review of the repo's own prior AI
> decisions (`docs/research-and-roadmap.md` Part 3's deferred-tracks section), and an
> evaluation of candidate features against the criteria in §1.
>
> **Verdict up front:** Cryo's existing AI philosophy is already unusually disciplined —
> the copilot answers only *verified questions* through deterministic SQL handlers with
> embedding-based routing (no freeform chat), health-change explanation is template
> narration (no LLM), the ML models are small and interpretable (KNN classifier, linear QPP,
> MAD/z-score anomaly detection, contextual bandit), and the repo has *already rejected* the
> fashionable-but-wrong options (RL scheduling, time-series foundation models). The gaps are
> therefore not "add AI" — they are: **one genuinely missing LLM use case (failure
> diagnosis), one bounded translation task (natural language → filter spec), wiring for
> models that exist but never run (QPP), and the evaluation loop (skillsets) that exists in
> the backend but has no UI.** Everything proposed below grounds in data Cryo already
> collects and fails safe without an LLM provider configured.

---

## 1. What "genuine" means here — the acceptance criteria

Derived from `PRODUCT.md` (trust-first, operator-first) and from the design properties of
the AI features that already work in this codebase. A proposed AI feature ships only if it
passes all six:

1. **Grounded** — every input comes from data Cryo already stores (stats, audit log, run
   events, query logs, plan JSON). No feature may require the LLM to "know" the fleet.
2. **Bounded output** — the model's output is either (a) a structured artifact that
   executes deterministically and visibly (a filter spec, a routed question ID), or (b)
   prose that is clearly labeled as narration, with every number passed through verbatim
   from a query — never generated.
3. **Fails safe** — with no AI provider configured (air-gapped installs), the surface
   degrades to the deterministic equivalent or hides; it never errors or blocks a workflow.
4. **Write-gated** — no LLM output triggers a write without explicit human confirmation
   through the existing confirm/guardrail machinery. This is already MCP policy
   (`cryo/mcp/server.py`: everything read-only except `run_preset`, which is documented as
   requiring explicit confirmation) — it becomes global policy.
5. **Auditable & evaluated** — every LLM call runs through a versioned skillset
   (`cryo/ai/skillset.py` — prompt + tools + outcome recording already built) and is logged
   to the observation lakehouse. If we can't measure whether the feature's outputs are
   accepted or corrected, we can't keep it honest.
6. **Cheaper than the alternative** — if a deterministic implementation is feasible and
   equally good, build that instead. `cryo/ai/explain.py` (template-based health-change
   narration) is the house style: it looks like an AI feature and contains no model. That
   is a compliment.

---

## 2. Audit of what exists today

### 2.1 Real and well-designed (keep, extend)

| Surface | What it actually is | Assessment |
|---|---|---|
| Copilot (`cryo/ai/copilot.py`, `/ai/copilot/*`) | **Verified questions**: NL is embedding-routed to deterministic SQL handlers (`_worst_tables`, `_compaction_backlog`, …); answers are query results, not generations | The correct architecture. Its problem is reach, not design — it's buried on the Intelligence page (§4.2 extends it) |
| Health-change explanation (`cryo/ai/explain.py`) | Deterministic before/after diff narration | Genuine "AI-feeling" UX with zero hallucination surface. Model for §4.3/§4.5 |
| Anomaly detection (`cryo/ml/anomaly_detector.py`) | MAD / robust / z-score over stats history | Right choice — the repo already evaluated and rejected Chronos/TimesFM for this data shape (sparse, per-table, low-frequency) |
| Health classifier (`cryo/ml/health_classifier.py`) | Few-shot KNN, evaluated in tests | Interpretable, cheap |
| Learned cost model (`cryo/ml/cost_model.py`) | Beats heuristic on synthetic data in tests | Feeds BCR terms in the smart-maintenance plan |
| Admission bandit (`cryo/governor/bandit.py`) | Contextual bandit with confidence gate | The auditable alternative to RL the repo deliberately chose |
| Union search (`cryo/ai/union_search.py`) | Embedding-based unionable-table search, tested, surfaced on Table Detail | Genuine discovery feature |
| PII classification (`cryo/ai/pii.py`) | Column classification, surfaced, feeds the fail-closed PII gate | Load-bearing for compliance |
| MCP server (`cryo/mcp/server.py`) | Tool registry: 7 read-only tools + gated `run_preset` | Right defaults; needs the hardening in §4.7 |
| LLM provider layer (`cryo/ai/llm.py`) | Anthropic / OpenAI / Azure abstraction | Sound; two fixes in §5 |

### 2.2 Built but not running (fix before building anything new)

| Item | State | Why it matters |
|---|---|---|
| QPP admission gate | `evaluate_qpp_for_job` has **zero call sites** — the governor never invokes it | A shipped model that never runs is the purest form of gimmick: it exists only in the README. Wire it (one call site in the admission loop) or remove the claim |
| Skillset lifecycle | Backend supports create/activate/rollback/outcome-recording; the `/skillsets` page only lists; four client functions unused | Skillsets are the **evaluation harness for every LLM feature in this doc** (§5.1). Without the promote/rollback UI, prompt changes ship unevaluated |
| Anomaly signal → triggers | `AnomalySignal` wiring specified in the smart-maintenance plan | Detection without actuation is a dashboard, not a feature |

### 2.3 Honest labels

`TableAiInsights` on Table Detail aggregates similarity, anomalies, classification, PII —
all real, all deterministic/ML. Nothing on the Intelligence page is a hollow LLM wrapper
today. The brand risk is the opposite one: **deterministic features labeled "AI"** invite
skepticism from exactly the operator audience Cryo targets. Recommendation: reserve the
"AI" label for LLM-backed surfaces; call the rest what they are (statistics, similarity,
classification).

---

## 3. What was already rejected — and stays rejected

For the record, so future proposals don't relitigate (`docs/research-and-roadmap.md` Part 3):

- **Full RL scheduling** (Decima-style) — sim-to-real transfer unsolved; unauditable
  policies gating production compaction. The bandit stays.
- **TS foundation models** (Chronos/TimesFM/Moirai) — degrade on Cryo's exact data shape.
  EWMA + seasonality (smart-maintenance plan §2.4) stays.
- **GNN lineage discovery** — needs fleet-scale column sampling and model serving; a
  separate product. Embedding similarity stays.
- **Causal savings attribution** — blocked on ≥90 days of paired outcome history; revisit
  when the data exists, not before.

---

## 4. Proposed genuine AI features, ranked

Ranked by (operator pain relieved × how much AI is *the* right tool) ÷ effort. Each entry
states its grounding data, failure mode, and guardrail. Features 1–5 are LLM-backed; 6 is
classic ML; 7 is agent-infrastructure.

### 4.1 Run-failure diagnosis — the single highest-value LLM feature Cryo can build

**What:** when a maintenance job fails, a "Diagnose" action produces: failure class (from a
fixed taxonomy: *commit conflict · engine OOM · catalog timeout/rate-limit · permission ·
SQL/dialect error · storage error · unknown*), the evidence (verbatim log lines, linked),
and the runbook next step for that class.

**Why this is genuinely AI-shaped:** the input is unstructured — Trino stack traces, Spark
EMR stderr, PyIceberg exceptions — and mapping messy text to a known taxonomy is the one
task in this product where an LLM decisively beats regex libraries (which rot per engine
version). Every *other* part is deterministic: the taxonomy is fixed, the runbook content is
curated markdown, the log lines are quoted not paraphrased.

**Grounding data (all exists):** `MaintenanceJob` error detail, run events (Redis/WS
stream), `AuditLog` rows, observation-lakehouse history for "has this table failed this way
before" (the repo's §3.2 research even documents the commit-conflict pattern this will most
often diagnose).

**Failure mode & guardrail:** wrong classification → wrong runbook page. Mitigations: the
taxonomy includes `unknown` and the prompt instructs abstention; the evidence lines are
displayed so the operator verifies in one glance; outcomes ("was this diagnosis right?"
thumbs) feed `recordSkillsetOutcome` — the existing promotion machinery decides whether a
prompt version is actually good. Read-only; zero write surface.

**Where:** Run detail page (`/runs/:runId`) on failed jobs, and the failed-tables panel.
**Effort:** ~1–2 weeks (prompt + skillset + one endpoint + UI card). **No-provider
fallback:** the card doesn't render; the raw error remains as today.

### 4.2 Natural language → filter spec ("find tables like…")

**What:** a search box on the Tables explorer that translates *"tables over 100 GB, >50%
small files, not compacted in two weeks, in the sales schema"* into the **existing**
filter-spec grammar — rendered as the same filter chips the operator could have clicked,
which they then execute (or edit) themselves.

**Why genuine:** filtering is the product's declared primary interaction (`PRODUCT.md`),
and the predicate grammar already exists (`lib/predicate.ts`, FilterBar, server-side filter
spec per `lld.md` §13). This is a bounded translation task: the output is a structured
artifact that is *visible before execution* — a hallucinated field name simply fails
validation against the known schema and is shown as an error chip. There is no way for the
model to be silently wrong, which is what separates this from a chatbot.

**Grounding:** the filter-spec JSON schema + field enum, sent in the prompt. **Guardrail:**
chips render for confirmation; the LLM never executes anything; invalid fields rejected by
the existing validator. **Fallback:** the box hides without a provider; embedding-only
routing to the copilot's verified questions (already built) can serve as a degraded mode.
**Effort:** ~1 week. This also composes with 4.7: the same translation endpoint becomes an
MCP tool, so an operator's own agent can query the fleet safely.

### 4.3 "Explain this plan" in PlanPreview

**What:** a narration panel in PlanPreview: which operations run in what order and why
(cooldowns, off-peak, chunk budgets), what the rendered SQL does on this engine, what the
verifier checked, expected load. `RATING_2026.md` E2 already asks for exactly this
affordance.

**Why genuine, and why hybrid:** ~80% is deterministic — the plan JSON, guardrail
decisions, and verifier verdict are structured; render them with `explain.py`-style
templates. The LLM's role is narrow: translating the *rendered SQL* into operator prose
("rewrites files smaller than 128 MB in 47 partitions, in 12 chunks…"), where dialect
variety makes templates brittle. Numbers are injected from the plan verbatim; the model
narrates around them.

**Guardrail:** the panel is labeled "explanation — the SQL below is what runs"; the SQL
remains primary. Deterministic template fallback without a provider (slightly stiffer
prose, same facts). **Effort:** ~1 week, mostly template work that is valuable on its own.

### 4.4 Anomaly root-cause brief

**What:** when an anomaly fires (null-rate spike, size drift, file-count explosion), attach
a short ranked-hypotheses brief: *"Null-rate spike on `orders.email` follows a schema change
in snapshot 8812 (writer: spark-etl-prod) two hours earlier; commit velocity is unchanged;
no similar drift on sibling tables."*

**Why genuine:** the correlation joins are deterministic (schema-change events, snapshot
summaries and writer identity, commit velocity, sibling-table comparison via the existing
similarity embeddings) — Cryo has all of these and today shows the anomaly *without the
context*, pushing the join work onto a human at exactly the moment they're paged. The LLM
only composes the pre-computed evidence into three ranked sentences.

**Guardrail:** every hypothesis carries its evidence link; hypotheses without evidence are
not emitted (the composer receives only the joined facts). Outcome thumbs feed skillset
promotion. **Effort:** ~2 weeks (the correlation queries are the real work and are
independently useful). **Fallback:** show the raw correlation table without narration.

### 4.5 Narrated maintenance digest

**What:** the existing scheduled email/Slack summaries gain a three-sentence narrative lead
("Fleet health improved B→B+; the 12 TB `events` compaction cleared the backlog; two tables
are new D-grades — both from the `cdc` schema") above the existing tables of numbers.

**Why genuine but last among the LLM features:** summarization is real value (digests are
skimmed in Slack, not studied) — but it's polish, not pain relief. Numbers verbatim,
template fallback, lowest risk, lowest urgency. **Effort:** days, reusing 4.1's plumbing.

### 4.6 Maintenance-job resource prediction (classic ML, not LLM)

**What:** predict CPU/memory for native-engine jobs from planner-known features
(`total_size_gb`, file count, delete ratio, partition count), as quantile regression —
structurally the sibling of the existing QPP model.

**Why genuine:** `docs/native-engine-scoping-research.md` §1 names this exact gap ("a
structurally similar model doesn't yet exist for 'how much CPU/mem will this compaction
job need'"), and it directly feeds Move 3's K8s Job sizing — replacing a static formula's
guesses with learned corrections from completed-job telemetry, with Kubernetes in-place
resize as the runtime correction for misses. **Effort:** ~1 week after Move 3 lands, on
the QPP code pattern (closed-form, no sklearn). **Prerequisite:** wire QPP first (§2.2) —
shipping a second unwired model would be farce.

### 4.7 MCP maturation: Cryo as a tool surface for the operator's own agents

**What:** finish the existing MCP server into something teams can safely point Claude (or
any agent) at: per-tool RBAC using the existing role decorators, audit-log rows for every
tool call, `simulate_preset`/`explain_health_change` exposed (already registered), the 4.2
translation endpoint as a query tool, and `run_preset` kept behind an explicit
capability flag that is **off by default**.

**Why genuine:** the agents that will operate on data platforms are the *user's* agents;
Cryo's job is to be a safe, legible tool surface, not to embed its own general agent. Both
Ryft ("Data Lake for AI Agents" — its literal homepage) and LakeOps marketed this; Cryo has
the server built and documented as dangerous. Hardening it converts a liability note in
`OSS_READINESS.md` §2.2 into a differentiator. **Effort:** ~1 week (auth plumbing + audit +
docs page with a copy-paste Claude Desktop/Code config).

---

## 5. Platform prerequisites (do these before/with feature 4.1)

1. **Close the evaluation loop**: finish the Skillsets UI (activate/promote/rollback +
   outcome stats — client functions already exist, page is read-only). Every feature above
   records outcomes; without the UI, nobody acts on them.
2. **Log every LLM call** to the observation lakehouse (prompt hash, skillset version,
   latency, token counts, outcome) — the archive exists; attach the writer.
3. **Refresh model defaults**: `cryo/ai/llm.py` pins `claude-sonnet-4-20250514` /
   `gpt-4o` — a generation stale. Make the default model a Settings field, not a code
   constant, so upgrades don't need releases.
4. **Token/cost budget setting** per provider (daily cap, per-call max) with a Prometheus
   counter — an ops tool that surprises you on your LLM bill has failed its own cost-
   transparency brand.
5. **Local/air-gapped path**: support an OpenAI-compatible base URL override (vLLM/Ollama)
   so self-hosted deployments — the entire customer base — aren't excluded from LLM
   features. The provider abstraction makes this small.

---

## 6. Anti-features — proposed and rejected here, with reasons

| Anti-feature | Why rejected |
|---|---|
| Freeform "Ask Cryo anything" chatbot | Unbounded output over a trust-first product; the verified-questions copilot is the correct ceiling for open input. Extending *routing* (4.2) is fine; extending *generation* is not |
| LLM choosing/queuing maintenance actions autonomously | Same auditability grounds on which the repo rejected RL scheduling; the governor/ROI gate is the decision layer, and it's deliberately interpretable |
| LLM-generated SQL executed against engines | The renderer + golden-test discipline exists precisely so SQL is deterministic per engine. Narrate SQL (4.3); never generate it for execution |
| "AI health score" | The A–F deduction model is explainable line-by-line; replacing it with a model regresses the product's most trusted artifact |
| Anomaly detection via foundation models | Already evaluated and rejected for this data shape; re-rejected |
| Embedding "semantic search" over docs/UI as a marquee feature | Nice-to-have; not operator pain. Ships only if free alongside 4.2's infrastructure |

---

## 7. Sequencing

| Order | Item | Effort | Depends on |
|---|---|---|---|
| 0 | §5.1–5.5 platform prerequisites + wire QPP (§2.2) | ~1 week | — |
| 1 | 4.1 Run-failure diagnosis | 1–2 weeks | Prereqs |
| 2 | 4.2 NL → filter spec | ~1 week | Prereqs |
| 3 | 4.3 Explain-this-plan (templates first, LLM narration second) | ~1 week | — |
| 4 | 4.7 MCP maturation | ~1 week | 4.2 (shares the translation tool) |
| 5 | 4.4 Anomaly root-cause brief | ~2 weeks | Correlation queries |
| 6 | 4.6 Resource prediction | ~1 week | Move 3 (K8s Job dispatch) + wired QPP |
| 7 | 4.5 Narrated digest | days | 4.1 plumbing |

Conflict rule: if any of this competes with the trust fixes in
`docs/UIUX_REVIEW_2026-07.md` §5 P0, the trust fixes win — an LLM diagnosis card on a page
that renders raw 404s makes the product look *less* trustworthy, not more.

---

## 8. Cross-references

| Topic | Where |
|---|---|
| Prior AI rejections (RL, TS foundation models, GNN) | `docs/research-and-roadmap.md` Part 3 |
| Skillset/outcome machinery | `cryo/ai/skillset.py`; UI gap in `docs/UIUX_REVIEW_2026-07.md` §4.6 |
| QPP dead-code finding | conversation audit 2026-07-08; `cryo/governor/capacity.py:238` |
| Resource-sizing gap | `docs/native-engine-scoping-research.md` §1, §6.3 |
| MCP warnings to convert into policy | `cryo/mcp/server.py` docstring; `docs/OSS_READINESS.md` §2.2 |
| Verified-questions copilot pattern | `cryo/ai/copilot.py` |
