# Cryo UI/UX Comprehensive Review — 2026-07-08

> Scope: every routed page, the navigation architecture, cross-cutting components,
> accessibility, and — critically — **backend capability that is built but not surfaced in
> the UI**. Method: file-level inspection of `web/src/` (verified against the working tree
> today, not against older audits), a Python diff of all 178 backend API endpoints against
> frontend references, a diff of all 175 `client.ts` exports against page/component usage,
> and a live-screenshot review of Table Detail. Where `docs/RATING_2026.md` already scored
> something, this doc goes past it rather than repeating it.
>
> **Verdict up front:** the UI's biggest problems are no longer visual polish — the wave-1/2
> fixes landed (skip link, `ErrorBoundary`, `PageBreadcrumbs`, `NotificationRegion`,
> `min-width:1100` removed). The biggest problems now are **architectural**: the primary
> product surface has no navigation entry, three cost pages compete for one job, two design
> specs in the repo contradict each other, ~15 fully-built backend features are invisible,
> and the one component built to standardize loading/error states (`QueryState`) is adopted
> by zero pages.

---

## 1. Architectural findings (fix these before any page-level polish)

### 1.1 The repo has two conflicting design specs — and the app follows the one that isn't `DESIGN.md`

| Question | `DESIGN.md` says | `docs/research-and-roadmap.md` Part 6 says | The app actually does |
|---|---|---|---|
| Font | Inter | IBM Plex Sans/Mono | IBM Plex (`styles.css:1-2`) |
| Default theme | Light, white surfaces | Dark by default, sidebar always dark | Dark sidebar always; light/dark toggle |
| Color system | OKLCH tokens, cobalt primary | Hex tokens, AntD blue `#1677ff` | Hex tokens |
| Sidebar | 224px | Dark rail `#0f172a` | ~212px dark rail |

**Why it matters technically:** every future UI PR (and every AI agent reading the repo)
has a 50% chance of following the wrong spec. `DESIGN.md` is positioned as canonical brand
guidance; Part 6 is positioned as "the canonical source for tokens, shell, patterns." Both
cannot be canonical. **Recommendation:** declare Part 6 + the shipped implementation as
canon (rewriting the app to Inter/OKLCH/light-first is weeks of churn for zero operator
value), then rewrite `DESIGN.md`'s token/typography sections to describe reality, keeping
its interaction rules and anti-pattern bans (those are followed and good). One doc, one
truth — this is the same docs-vs-code drift class `OSS_READINESS.md` §1.1 calls the repo's
signature failure mode.

### 1.2 Information architecture: the primary surface is unreachable from the nav

`App.tsx` nav items: Health, Catalogs, Scanner, Intelligence, Governance, Queries, Cost,
SLA, Optimize, Smart Maintenance, Presets, Runs, Connections. **There is no "Tables" item**,
yet `/tables` (Table Explorer with FilterBar, bulk selection, preset assignment) is routed
and is, per `PRODUCT.md`, the product's primary interaction: *"Make filtering the primary
interaction."* Today an operator reaches the fleet view only by drilling through Catalogs or
by knowing the URL.

**Recommendation:** add **Tables** to the Inventory group, first position. This is a
two-line `App.tsx` change with the highest usability-per-line ratio available anywhere in
the codebase.

Other IA defects, ranked:

1. **Three cost surfaces**: `/cost` (Cost.tsx, 88 LoC), `/cost-insights` (CostInsights.tsx,
   794 LoC — routed, **not in nav**), `/attribution` (CostAttribution.tsx — hidden
   intentionally per `OSS_READINESS.md`). An operator asking "what does my lake cost?" has
   one nav entry (`Cost`) that is the *thinnest* of the three pages. **Recommendation:**
   merge CostInsights into Cost as tabs (the `/cost?tab=` pattern already exists for AWS
   integrations — reuse it), keep Attribution hidden until real. One cost destination,
   consistent with the RATING E1 naming-coherence finding.
2. **`/skillsets` is routed but not in nav** — and the page is a read-only shell (§4.4).
   Either finish it or gate it; a URL-only page that renders a bare list is discoverable via
   the OpenAPI docs and looks abandoned when found.
3. **`/jobs/:jobId` alias still mounted** (`App.tsx:260`) redirecting to `/runs/:jobId` —
   the redirect pattern `Navigate to="/runs/:jobId"` does **not interpolate params** in
   React Router; this redirects to the literal string `/runs/:jobId`. That's a bug, not
   just naming debt. Fix with a param-aware redirect component or drop the alias.

### 1.3 Fonts load from Google's CDN — in a product whose comparison-table wedge is "data never leaves your infra"

`styles.css:2` imports IBM Plex from `fonts.googleapis.com`. Technical consequences: (a)
every operator browser session pings Google — hostile to the air-gapped/compliance
deployments that choose self-hosted tools, and directly at odds with the README's
differentiation row; (b) in a locked-down network the fonts fail and the UI silently falls
back with layout shift. The roadmap Part 6 §8 already lists self-hosting as a todo.
**Recommendation:** vendor the woff2 files into `web/public/fonts/` with `@font-face` +
`font-display: swap`. ~1 hour, removes a real trust inconsistency.

### 1.4 There is no heading hierarchy — zero `<h1>`–`<h6>` in 12k LoC of pages

Measured today: 0 native heading elements. `PageTitle.tsx` only sets `document.title` — it
renders no DOM heading. Ant `Typography.Title` usage is sparse. Screen-reader users get a
tab-title change and then a flat wall of divs; WCAG 2.4.6/1.3.1 both implicated, and
`PRODUCT.md` commits to WCAG 2.2 AA. **Recommendation:** make `PageTitle` render a visible
`<Typography.Title level={1}>` (styled to the existing 24px scale) in addition to setting
`document.title`, and use `level={2}` for section titles. Because `PageTitle` is already a
shared component, this fixes every page from one file.

### 1.5 `QueryState` is adopted by zero pages

The component exists (`components/QueryState.tsx`, tested) and was built precisely to
standardize loading/empty/error rendering — and no page imports it. The consequence is
visible in the Table Detail screenshot: a raw `Not Found` rendered as a red error panel
(§2.4). **Recommendation:** adopt `QueryState` in every `useQuery` consumer, with a lint
rule (custom ESLint or grep-in-CI) that flags `isError` handled ad hoc. Mechanical,
page-by-page, and it retires the whole class of raw-HTTP-error UI.

### 1.6 CSS debt is growing, not shrinking

`styles.css` is now 2,915 lines (was 2,535 at rating time) with **178** `!important`
declarations (was 170). The trend is the wrong direction because page-specific CSS keeps
landing in the global file. **Recommendation:** freeze `styles.css` (CI check: line count
must not increase), route new styling through AntD `ConfigProvider` tokens, and pay the
existing debt down opportunistically per the RATING §10 plan. No visual change; this is
purely about stopping the bleeding.

---

## 2. Page-by-page review

### 2.1 Datalake Health (`/health`, landing page) — 328 LoC

Solid landing shape (KPI strip → grade distribution → offenders). Issues:
- The cost-savings waterfall (`CostSavingsWaterfall.tsx` exists) answers "what will
  maintenance save" — verify it renders here with data; the roadmap Part 7 spec places it
  on this page.
- Grade distribution must not be color-only (see §3).
- **Add**: "event-driven coverage %" and ROI-gate savings widgets when Smart Maintenance
  ships its data (per the smart-maintenance plan §UI — two widgets, not four).

### 2.2 Catalogs (`/catalogs`) — 29 LoC shell + `pages/catalog/`

Fine as a drill-in tree. Once Tables gets a nav entry (§1.2), Catalogs' role narrows to
"browse by source hierarchy" — make that explicit in the page intro line so the two entry
points don't read as duplicates.

### 2.3 Table Explorer (`/tables`)

The FilterBar + chips + server-driven "select all matching" machinery is the strongest UX
in the product — it correctly implements the filter-spec model from `lld.md` §13. Gaps:
- **No nav entry** (§1.2) — everything else is secondary to that.
- Persist active filters in the URL (deep-linkable saved views); TanStack Query + a
  `useSearchParams` serialization of the existing filter spec is ~a day and turns every
  operator's triage query into a shareable link.
- Wire `VirtualizedTable` (built, tested, unused here) once fleets exceed ~2k rows, and
  cursor pagination per Move 23.

### 2.4 Table Detail (`/tables/:tableId`) — 2,448 LoC, **9 tabs**

> The most important page in the product gets its own deep dive in **§6** (full anatomy,
> add/modify inventory, tab-consolidation proposal, orphaned-component findings). The
> summary defects below are expanded there.

The most information-dense page and the one with the most trust-damaging defects (verified
against a live screenshot):

1. **Raw 404 in Compliance & Protection**: "Failed to load compliance status — Not Found."
   The client calls `/tables/{id}/compliance` and renders the HTTP error verbatim. A table
   with no compliance data is an *empty state* ("No deletion-tracking data yet"), not a
   failure. This single panel undermines the page's authority. Fix via `QueryState` with a
   404→empty mapping.
2. **"Savings / mo: US$0"** beside a D grade and "needs compaction" reads as a broken
   product. If the projection isn't computed, show "Not estimated"; if the honest number is
   ~$0 because the table is 0.8 GB, the benefit is query latency — say that instead. Never
   show a confident zero next to a high-priority flag.
3. **Internal sentinel leak**: Partition Col renders `_unpartitioned`. Map sentinels to
   operator language ("Unpartitioned") at the formatter layer, and collapse the
   Partitions=1 KPI when unpartitioned.
4. **State shown in triplicate**: preset appears in header subtitle, Act-now card, and
   right rail (two of them editable — a write-write conflict surface, not just clutter);
   last scan/commit shown twice; grade expressed four ways (D pill, HIGH pill, "Needs
   compaction" tag, full-width banner). Pick one canonical location per fact. DESIGN.md
   explicitly bans redundant labels.
5. **"Act now" naming collision**: section header and primary button share the label.
   Buttons must be verb-specific (`Run compaction`), per DESIGN.md's own copy rules.
6. **9 tabs is 3 too many** (`overview, schema, cost, ai, quality, activity, candidates,
   unionable, column-stats`) — the tab bar truncates at desktop width. Merge: `candidates`
   + `unionable` + `column-stats` are all "analysis" (one tab, three sections);
   `ai` + `quality` overlap in content sourcing. Target ≤6 tabs.
7. **Unexplained microcopy**: "0.0 pts/day" under the gauge has no label; threshold chip
   row uses an unlabeled arrow (`100.0% … → < 20.0% …`) — label Current vs Target.
8. The page is still a 2,448-LoC monolith; the overview tab (~700 LoC) extraction from
   RATING §10 remains the enabler for all of the above as small PRs.
9. **Missing content that the backend already serves** (see §4.1): partition breakdown and
   stats history — the roadmap Part 6 §4.5 spec for this page lists both, the endpoints and
   client functions exist, and nothing renders them.

### 2.5 Connections (`/connections`) — 2,245 LoC

Functionally rich (test-before-save, AWS panel, query-log ingestion fields — all good
patterns). Issues: it's the second-largest monolith; the tab set (Lakehouses / Engines / AI
Providers / Slack / Ops) mixes infrastructure with notification settings. **Recommendation:**
split Slack/Ops into a Settings surface when one exists; decompose per the established
`pages/connections/` pattern (already started). Also ensure secrets never render back after
save (DESIGN.md rule) — the redact layer exists server-side; verify the form doesn't echo
`secret_ref` contents.

### 2.6 Scanner (`/scans`, `/scans/:runId`) — 784 LoC

Live log + chunk progress is strong. Two gaps: surface `consecutive_scan_failures` streaks
(data exists per README) as a sortable column — a silently failing table is this page's
whole reason to exist; and the scan-cost preflight (strategy Move 8) belongs here when it
lands.

### 2.7 Runs (`/runs`, `/runs/:runId`) + JobDetail — 235 + 283 LoC

Healthy size, WS console works, manual-reconnect is the right call. Gaps: no
trigger-source column (SCHEDULE/EVENT/ANOMALY/MANUAL — schema supports it once smart
maintenance lands); "Cancel/Retry" need keyboard handlers; the `/jobs/:jobId` redirect bug
(§1.2.3). Add "clone run" from history (RATING C4 suggestion, still unimplemented, cheap).

### 2.8 Presets (`/presets`) — 545 LoC

Good drawer-based editing. Add: a coverage indicator ("this preset currently matches N
tables" — the filter-spec machinery can answer this with one count endpoint) so operators
see blast radius *before* saving a rule change. That's a guardrail, not a nicety: rule
edits silently re-target fleets.

### 2.9 Layout Optimization (`/optimize`) — 724 LoC

Exists and is wired to recommendations. Per the smart-maintenance plan: add approve→run /
dismiss actions through the existing run machinery, confidence badges, and "requires Spark"
labels for Trino destinations (a recommendation with an un-runnable action is worse than no
recommendation). **Also surface bloom-filter recommendations here** — see §4.2.

### 2.10 Smart Maintenance (`/smart-maintenance`) — 338 LoC, new

Correctly shipped as one page with tabs (per the plan's rev-2 scope cut). Review of the
implementation: uses `Statistic` cards + `Descriptions` for tier counts — fine and
DESIGN.md-compliant. Ensure: (a) the shadow-mode banner is present on the ROI tab whenever
`roi_gate_shadow_mode` is on — decisions-not-enforced must be unmissable; (b) tier counts
link to the Tables explorer pre-filtered by tier (the filter-spec grammar supports it) so
the dashboard is a launchpad, not a dead end.

### 2.11 Intelligence (`/ai`) — 455 LoC, Governance (`/governance`) — 766 LoC, Queries (`/queries`) — 185 LoC

All three are telemetry-dependent. Per `OSS_READINESS.md` §2.4, each must state its data
dependency when empty ("Requires query-log ingestion — configure on the Engines tab", with
a link) instead of rendering empty charts. Governance at 766 LoC is approaching monolith
territory; watch it. QueryPerformance is thin — candidate for merging into Queries/Cost
once QPP surfaces (§4.3).

### 2.12 SLA (`/sla`) — 284 LoC

Freshness is correctly commit-time-based (the FORMATS '26 fix). Add `scan_staleness_hours`
as a secondary column so operators can distinguish "data is stale" from "Cryo hasn't
looked recently" — the API already returns both.

### 2.13 Skillsets (`/skillsets`) — 93 LoC

Read-only shell: imports only `listSkillsets`; the activate/rollback/create/outcome client
functions exist and are unused (§4.4). Not in nav. Decide: finish or gate.

### 2.14 Login / LoginCallback

Dev-auth flow is fine. One issue from RATING still open: auth errors render as small inline
text — use an `Alert` banner. Low effort.

---

## 3. Accessibility audit (measured 2026-07-08)

| Check | State | Assessment |
|---|---|---|
| Skip link | ✅ `App.tsx:195` → `#main-content` | Done |
| Error boundary | ✅ `ErrorBoundary.tsx`, wired in App | Done |
| `prefers-reduced-motion` | ✅ two media blocks | Done |
| Live region | ⚠️ 1 `aria-live` (`NotificationRegion`) | Exists — but verify run-console and scan-progress updates announce through it; WS-driven DOM swaps are silent otherwise |
| `aria-*` total | ⚠️ 24 (20 `aria-label`) across ~12k page LoC | Still sparse; icon-only buttons in tables (copy path, tab overflow "…", right-rail icons) need labels |
| Headings | ❌ **0** `<h1>`–`<h6>` | §1.4 — the single biggest a11y gap, fixable in one shared component |
| Color independence | ⚠️ grade/status chips carry letters; verify charts (grade distribution, tier pie) have text equivalents | Add sr-only data tables for charts per roadmap Part 7 §11 |
| Contrast | Light-theme `--ink-faint`-class tokens were failing at rating time | Re-verify post-wave; gate with axe |
| Automated gate | ❌ none | Move 21: axe-core in Playwright, fail CI on serious/critical — this is what keeps all of the above from regressing |

Keyboard: AntD gives table/menu navigation for free, but the bespoke surfaces (FilterBar
chip removal, PlanPreview sections, RunConsole scrollback) need explicit tab-order and
Enter/Space handling verification — do it once, in the Playwright spec, not by hand.

---

## 4. Built in the backend, invisible in the UI

Method: all 178 backend endpoints are referenced by `client.ts` (only one endpoint has no
client binding at all), but **15 client functions are never called by any page or
component**. Each row below is a shipped, tested-or-at-least-wired backend capability with
zero UI. This is the cheapest feature inventory in the repo — the hard half is done.

| # | Capability | Unused client functions / endpoint | Where it should surface, and why there |
|---|---|---|---|
| 1 | **Partition breakdown + stats history** | `getTablePartitions`, `getTableHistory` | Table Detail. The roadmap Part 6 §4.5 spec for this page literally lists "partition breakdown → file-count history" and neither renders. Partition-level data is what justifies partition-aware planning to a skeptical operator — it's the product's core differentiator made visible. Highest-value row in this table. |
| 2 | **Bloom-filter column recommendations** | `GET /bloom-filter-recommendations/{table_id}` (the one endpoint with no client binding at all) | LayoutOptimization page + Table Detail analysis tab. The roadmap calls this "unique feature — no competitor surfaces this" — it is currently invisible, which is the worst place for a unique feature to be. |
| 3 | **Plan Verifier** (arXiv:2510.09567) | `verifyPlan` | Inside `PlanPreview` as a verification verdict panel ("7 invariants checked, 0 violations") before the Run button. Technically ideal fit: the verifier's whole purpose is pre-enqueue rejection, and PlanPreview is the pre-enqueue surface. Also converts a README research claim into something demo-able. |
| 4 | **QPP predictions** (arXiv:2606.03464) | `predictQPP`, `getQPPConfidence`, `recordQPPObservation` | Predicted p95 + model confidence in PlanPreview's load-impact section. **Caveat: wire the backend first** — `evaluate_qpp_for_job` currently has zero call sites in the governor; surfacing predictions the governor ignores would be a false promise. Backend wiring, then UI. |
| 5 | **Observation Lakehouse** (arXiv:2512.02795) | `searchObservations`, `getObservationStats`, `compactObservations` | An "Observations" section in Governance (it's an audit/forensics tool, and Governance is the audit surface). Search + stats read-only first; `compact` is admin-only. |
| 6 | **Skillset lifecycle** | `getActiveSkillset`, `createSkillset`, `rollbackSkillset`, `recordSkillsetOutcome` | Finish `/skillsets`: show active version, promote/rollback with confirm, outcome stats (the metrics blob already carries acceptance rate / mean score). Or gate the route behind an admin flag until then. A half-page is worse than either. |

Sequencing note: rows 1–3 need no backend work at all. Row 4 needs one governor call site.
Rows 5–6 need only UI. Every row also closes a gap this review's companion audit found: the
README claims these papers are product features; today four of the six are API-only, which
an evaluator will read as vaporware when they click around and find nothing.

---

## 5. Prioritized sequence

Ranked by (operator trust impact) ÷ (effort). Each item names its technical justification.

**P0 — Trust integrity (days, no backend changes)**
1. Map 404s to empty states via `QueryState` adoption, starting with Table Detail's
   compliance panel (§2.4.1) — raw HTTP errors on a healthy page are the fastest way to
   lose evaluator confidence.
2. Fix "US$0 savings" display logic and the `_unpartitioned` sentinel (§2.4.2–3) —
   confident-but-wrong numbers violate the product's only real moat: being trustworthy.
3. Fix the `/jobs/:jobId` non-interpolating redirect (§1.2.3) — it's broken today.

**P1 — Information architecture (days)**
4. Add **Tables** to the nav (§1.2) — two lines, unblocks the primary workflow.
5. Consolidate the three cost surfaces into `/cost` tabs (§1.2.1).
6. Declare the design-spec canon and rewrite `DESIGN.md` tokens to match reality (§1.1) —
   until this is done, every UI contribution is a coin flip.

**P2 — Surface the hidden backend (1–2 weeks, mostly frontend)**
7. Partition breakdown + history charts on Table Detail (§4.1).
8. Plan Verifier verdict in PlanPreview (§4.3).
9. Bloom-filter recommendations in LayoutOptimization (§4.2).
10. Telemetry-dependency empty states on Intelligence/Governance/Queries (§2.11).

**P3 — Accessibility baseline (days)**
11. `PageTitle` renders a real `<h1>` (§1.4) — one component, every page.
12. Label all icon-only buttons; verify live-region announcements for run/scan progress.
13. axe-core CI gate (Move 21) so none of this regresses.

**P4 — Structural debt (ongoing, opportunistic)**
14. Table Detail: 9→6 tabs + overview-tab extraction (§2.4.6, §2.4.8).
15. Self-host fonts (§1.3). Freeze `styles.css` growth in CI (§1.6).
16. Finish or gate Skillsets (§4.6); QPP UI after the governor call site lands (§4.4).

Conflict rule: if P2 competes with P0/P1 for time, P0/P1 win — surfacing more features on
top of a page that renders raw 404s compounds the trust problem rather than diluting it.

---

## 6. Deep dive: Table Detail (`/tables/:tableId`)

This page is where an operator decides whether to trust Cryo with a destructive action on
a production table — it deserves a review at the level of individual regions. Everything
below was verified against `TableDetailRedesign.tsx` (2,448 LoC), the 16 components in
`components/table-detail/`, the page's actual API usage (29 client functions), and the live
screenshot.

### 6.1 Anatomy today

```
Breadcrumbs → Title row (name · grade pill · HIGH pill · "Needs compaction" tag)
Subtitle row (preset · last scan · last commit · s3 path + copy)
[Refresh stats]
KpiStripe: health score · size · records · partition col · savings/mo · partitions · files · small files · snapshots
─────────────────────────────────────────────┬──────────────────────
Tabs (9):                                     │ Right rail:
  overview   (inline, ~712 LoC: gauge,        │   Preset & Engine
              grade banner, ActNowSection,    │   (assign, last scan/commit,
              diagnostics, HealthScore-       │    Run maintenance, Preview,
              Breakdown, StorageDonut,        │    Manifest)
              signals, trends)                │   Compliance & Protection
  schema · cost · ai · quality · activity     │   (status, Protect Snapshot)
  candidates · unionable · column-stats      │
─────────────────────────────────────────────┴──────────────────────
PlanPreview (modal/drawer, on demand)
```

**Credit where due — this page surfaces more backend than any other.** It already renders:
compliance status, silent-data-loss events, per-partition benefit scores, ROI breakdown,
trigger signals, health trends, PII classification, data-quality profile, partition-evolution
recommendation, audit timeline, alert subscriptions, snapshot protection, a standalone
manifest-rewrite quick action (Move 11 — shipped and wired here), AI insights, compaction
analytics, and a diagnosis panel. The problems are not missing effort; they are
**composition, duplication, and four finished components that were never plugged in**.

### 6.2 Finding: four table-detail components are built and imported by nothing

| Component | State | What it means |
|---|---|---|
| `table-detail/OverviewTab.tsx` | **0 imports** | The overview-tab extraction that `RATING_2026.md` §10 calls the next decomposition target *already exists* — the page still renders its ~712-LoC inline overview (lines ~643–1355) instead. Wiring this in is the single cheapest structural win on the page. |
| `table-detail/PartitionHistogram.tsx` | **0 imports** | A partition visualization, orphaned — pairing exactly with the unused `getTablePartitions` client function (§4.1). The partition story was built twice, backend and component, and never connected. |
| `table-detail/RecommendedActions.tsx` | **0 imports** | Overlaps `ActNowSection` (which is wired). Either it's the intended replacement or it's dead — decide and delete one; two near-duplicate action components is how the next contributor picks the wrong one. |
| `table-detail/SectionNav.tsx` | **0 imports** | An anchor-style section navigator — likely built for a scroll-based layout alternative to tabs (relevant to §6.5). Wire it or remove it. |

Dead components are not neutral: they inflate the bundle via any barrel exports, mislead
code search, and rot against the API they were written for.

### 6.3 What to MODIFY (defects, worst first)

1. **Compliance panel renders a raw `Not Found`** (screenshot, right rail). The client calls
   `getComplianceStatus` and surfaces the HTTP error verbatim. 404 here means "no
   deletion-tracking data for this table" — an empty state with a one-line explanation and
   a link to the compliance docs, not a red failure. Route through `QueryState` (§1.5) with
   an explicit 404 mapping. *This is the top P0 of the whole review because it sits on the
   page where trust is decided.*
2. **`Savings / mo: US$0` beside grade D / "needs compaction."** Three honest options:
   "Not estimated" when the projection hasn't run; the real dollar figure when it has; or —
   when the true number is ~$0 because the table is small — reframe the KPI as the actual
   benefit ("query latency", from the benefit-score data the page already fetches). A
   confident zero next to a high-priority flag reads as a broken calculator.
3. **State triplication.** Preset is displayed/editable in three places (subtitle,
   ActNowSection's "Engine preset" select, right-rail "Assign Preset") — two editable
   controls for one value is a write-write conflict surface. Last scan/commit shown twice.
   Grade expressed four ways (pill + HIGH pill + tag + full-width banner). **Rule: one
   editable location (right rail), one display location (title row), delete the rest.**
   The banner should collapse into the gauge caption — the D pill already communicates it.
4. **`_unpartitioned` sentinel leaks** into the Partition Col KPI. Map sentinels in
   `helpers.tsx` formatters ("Unpartitioned"), and when unpartitioned, drop the
   "Partitions: 1" KPI entirely — it's noise for 1-partition-by-definition tables.
5. **KPI stripe redundancy**: "Files 125" and "Small files 125 (100%)" duplicate each other
   for this table; the stripe has 9 cells competing for scan attention. Merge to
   "Files 125 · 100% small" in one cell; cap the stripe at 7 cells.
6. **"Act now" naming collision** — section header, count badge, and the primary button all
   say "Act now"; the button must be verb-specific (`Run compaction`) per DESIGN.md. The
   Preview button here duplicates the right-rail Preview — keep the card's (it carries the
   action context), make the rail's open the same drawer.
7. **Grade banner uses error semantics for a state.** ✕-icon + red for "Grade D" treats a
   chronic condition as an event. Use warning styling; reserve error styling for actual
   failures (scan failed, run failed).
8. **Unlabeled numbers**: "0.0 pts/day" under the gauge (health trend — label it or hide at
   zero history); the threshold chip row's unlabeled arrow (`100.0% small files → <20%`)
   should read "Current 100% · Target <20%".
9. **`Manifest` button** (right rail) is noun-only — it triggers `runManifestRewrite`, a
   real write action. Label it `Rewrite manifests` and gate it with the same confirm
   pattern as Run maintenance.
10. **Tab bar truncates at desktop width** — a symptom of §6.4.

### 6.4 What to MODIFY structurally: 9 tabs → 5

Current: `overview · schema · cost · ai · quality · activity · candidates · unionable ·
column-stats`. Three of these (`candidates`, `unionable`, `column-stats`) are thin wrappers
around single components; `ai` and `quality` draw from the same profiler/insights sources.
Proposal:

| New tab | Absorbs | Rationale |
|---|---|---|
| **Overview** | overview (via the already-built `OverviewTab`) | Unchanged role; finally extracted |
| **Structure** | schema + column-stats + **new Partitions section (§6.5.1)** | One physical-layout destination: columns, stats sketches, partitions |
| **Health & Quality** | quality + ai insights + silent-data-loss | One "is the data trustworthy" destination; AI insights are commentary on quality signals, not a separate domain |
| **Cost** | cost + candidates | Compaction candidates *are* the cost-reduction worklist; benefit scores already render in both |
| **Activity** | activity + unionable | Timeline + discovery; unionable is a discovery affordance, low-frequency, doesn't earn a top-level tab |

Five tabs fit without truncation at 1280px, and each answers one operator question
(DESIGN.md's stated test). Implementation is mostly moving `items` array entries — the
content components are already extracted.

### 6.5 What to ADD (data already available, zero backend work)

1. **Partitions section** — the page's biggest content gap. `getTablePartitions` (unused),
   `PartitionHistogram` (orphaned), and per-partition benefit scores (already fetched via
   `getTableBenefitScores`) combine into: a partition table (name, size, files, small-file
   %, skew, benefit score, last compacted) + the histogram. Technical justification: Cryo's
   core differentiator is *partition-aware* planning — the roadmap Part 6 §4.5 spec for
   this page lists "partition breakdown" explicitly, and today an operator cannot see the
   unit the planner actually operates on. Put it in the Structure tab; for unpartitioned
   tables render one line ("Unpartitioned — planner treats the table as a single unit"),
   which also retires defect §6.3.4.
2. **Stats history charts** — `getTableHistory` (unused) over `TableStatsHistory`: file
   count, total size, small-file %, snapshot count over time. The page already imports
   Recharts `LineChart`/`Area` and renders health trends, so this is one more chart block
   in Overview. Justification: trend direction is what distinguishes "needs compaction
   once" from "degrading weekly — fix the writer"; the health-trend line alone can't show
   *which* metric is driving decay.
3. **Verifier verdict in PlanPreview** — `verifyPlan` (unused, §4.3): render
   "N invariants checked · M violations" between the rendered SQL and the Run button.
   PlanPreview is pre-enqueue; the verifier is a pre-enqueue check; this page owns
   PlanPreview.
4. **Bloom-filter recommendation card** — the endpoint has no client binding at all (§4.2);
   surface per-table in the Structure tab next to partition-evolution recommendation, which
   already renders here (the pattern to copy is on the same page).
5. **Before/after effectiveness on the audit timeline** — the Activity tab already renders
   audit rows with file counts; compute and show `input_files → output_files (Nx)` per
   OPTIMIZE entry. Justification: Move 13's effectiveness ratio needs a home, and the
   timeline is where an operator asks "did that run actually help?"
6. **Deep-linkable tabs and sections** — `?tab=structure` URL state (TanStack Query
   coexists fine with `useSearchParams`). Operators paste links in incident channels; a
   link that always lands on Overview loses the context they meant to share.
7. **Prev/next table navigation** — the explorer's filtered result set is in query cache;
   carry ordering context so the header offers "← prev · 3 of 47 in filter · next →".
   Triage is list-shaped; forcing a round-trip to the explorer per table doubles the clicks
   of the product's core loop.

### 6.6 Right rail: rationalize into an action column

Today the rail mixes state display (preset, timestamps), actions (Run, Preview, Manifest),
and a broken panel (compliance). Target: **the rail is the single action + protection
column** — Assign preset (sole editable location), Run maintenance / Preview plan /
Rewrite manifests (all confirm-gated), Protect snapshot, Alert subscriptions, and the
compliance status as a quiet labeled state ("Deletion tracking: none recorded"), not an
error card. Timestamps move to the subtitle row only.

### 6.7 Page-specific accessibility

- The grade gauge is SVG with a number — needs `role="img"` + `aria-label="Overall health
  56 of 100, grade D"`; same for `StorageDonut` (sr-only table per roadmap Part 7 §11).
- Tab list must be arrow-key navigable (AntD default — verify it survived the custom
  label components) and each tab panel needs a heading once §1.4 lands.
- `CopyOutlined` path button, KPI info icons, and the diagnostics expander are icon-only —
  all need `aria-label`s; they are most of this page's share of the 20-label deficit.
- The Act-now card's count badge ("1") is meaningless to a screen reader — "1 recommended
  action" as accessible text.

### 6.8 Sequence for this page alone

| Order | Item | Effort | Type |
|---|---|---|---|
| 1 | Compliance 404 → empty state; `US$0` logic; `_unpartitioned` mapping (§6.3.1–4) | ~1 day | Modify |
| 2 | Wire `OverviewTab`, delete or wire `RecommendedActions`/`SectionNav` (§6.2) | ~1 day | Structure |
| 3 | Dedup preset/timestamps/grade; Act-now + Manifest button copy (§6.3.3/6/9) | ~1 day | Modify |
| 4 | Partitions section from `getTablePartitions` + `PartitionHistogram` (§6.5.1) | 2–3 days | Add |
| 5 | Stats-history charts (§6.5.2); effectiveness ratios on timeline (§6.5.5) | 2 days | Add |
| 6 | Tab consolidation 9→5 (§6.4); deep-linkable tabs (§6.5.6) | 2–3 days | Structure |
| 7 | Verifier verdict + bloom-filter card (§6.5.3–4); prev/next nav (§6.5.7) | 2–3 days | Add |
| 8 | Page a11y pass (§6.7) | 1 day | Modify |

Items 1–3 are pure fixes with no design decisions; 4–5 surface already-paid-for backend;
6–8 are the structural payoff. Nothing here requires a backend change except optionally a
count field for prev/next context.

---

## 7. Cross-references

| Topic | Where |
|---|---|
| Prior scored audit (baseline this doc extends) | `docs/RATING_2026.md` §10 |
| Design rules cited throughout | `DESIGN.md`, `PRODUCT.md` |
| Competing UI spec (canon decision needed) | `docs/research-and-roadmap.md` Part 6–7 |
| Launch-tier vs telemetry-dependent surfaces | `docs/OSS_READINESS.md` §2 |
| Moves referenced (21, 22, 23, 8) | `docs/strategy-next-moves-2026-07.md` |
| Smart Maintenance UI scope | `.cursor/plans/smart_maintenance_system_7ff6e470.plan.md` §Layer 5 |
| Research-paper feature audit (companion to §4) | conversation audit 2026-07-08; README "Research foundations" |
