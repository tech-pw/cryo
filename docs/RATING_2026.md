# Cryo Codebase Rating — 2026-07-07

A deep, evidence-based audit of the **Cryo** repository at the state of
`main` on 2026-07-07. Rates the product across **UI, Accessibility, Usability,
Engineering, and Experience** using 25 weighted attributes. Intended for
maintainers, contributors, and AI agents picking up the codebase.

> Methodology: file-level inspection with `cloc`-style counts, targeted
> `grep` audits for a11y/security/code-smell markers, and per-attribute
> rubric scoring (0–10 anchors in §2). Backend/frontend split is roughly
> 44k LoC Python vs 30.5k LoC TS/TSX/CSS.

---

## 1. Rating framework (25 attributes)

| Lens | Weight | Attributes |
|------|-------:|------------|
| **A. User Interface** | 15% | A1 Visual consistency · A2 Hierarchy & density · A3 Responsive layout · A4 Component reuse · A5 Loading/empty/error |
| **B. Accessibility** | 10% | B1 Keyboard & focus · B2 Semantic HTML & ARIA · B3 Color & contrast · B4 Assistive tech · B5 Motion & cognitive load |
| **C. Usability** | 20% | C1 Onboarding · C2 Navigation · C3 Discoverability · C4 Task efficiency · C5 Feedback & observability |
| **D. Engineering** | 35% | D1 Modularity · D2 Type safety · D3 Testing · D4 Performance · D5 Security · D6 Docs · D7 Ops |
| **E. Experience** | 20% | E1 End-to-end coherence · E2 Trust & transparency · E3 Dev/operator experience |

**Scoring:** each attribute 0–10. Lens score = mean of attributes. Overall =
weighted sum × 20 → max 200.

---

## 2. Rubric (0–10 anchors)

| Score | Anchor |
|------:|--------|
| 0–1   | Absent or misleading; actively harmful. |
| 2–3   | Scaffolding only; major gaps; many violations. |
| 4–5   | Functional but rough; some good patterns, some smells; not production-grade. |
| 6–7   | Solid; mostly consistent; a few known gaps tracked in docs. |
| 8–9   | Production-grade, idiomatic, observable; minor polish items. |
| 10    | Exemplary; documented as a model; rare. |

---

## 3. Headline numbers (evidence at evaluation time)

| Metric | Value | Source |
|---|---:|---|
| Frontend TS/TSX/CSS LoC | 30,508 | `find web/src … | xargs wc -l` |
| Backend Python LoC | 44,087 | `find cryo … | xargs wc -l` |
| Backend test files | 75 | `ls tests | wc -l` |
| Frontend test files | 21 | `find web/src -name "*.test.*" | wc -l` |
| E2E specs | 1 (`smoke.spec.ts`) | `ls web/e2e` |
| `aria-*` attributes in `web/src` | **15** (all `aria-label`) | grep |
| `role="…"` attributes in `web/src` | **4** (3× `img`, 1× `navigation`) | grep |
| `<h1>–<h6>` elements in `web/src` | **0** | grep |
| `aria-live` regions in `web/src` | **0** | grep |
| Skip-to-main-content link | **none** | grep |
| React error boundary in `web/src` | **none** | grep |
| `!important` declarations in `styles.css` | **170** | `grep -c` |
| `body { min-width: 1100px }` | **present** | `styles.css:63` |
| `console.*` in `web/src/pages` | 0 | grep |
| `@media (max-width: …)` breakpoints | 3 (900px × 2, 1280px × 1) | `styles.css` |
| Top monolithic file (frontend) | `pages/TableDetailRedesign.tsx` — **2,590 LoC** | wc -l |
| Top monolithic file (backend)  | `cryo/api/extensions.py` — **2,352 LoC** | wc -l |
| Files ≥1,000 LoC | 6 backend, 1 frontend | wc -l |
| Backend subpackages under `cryo/` | 40+ | `ls cryo` |
| OpenAPI tags | 11 (well-defined) | `cryo/api/app.py:33-58` |
| Dep manifests | **3** (`pyproject.toml`, `Pipfile`, `requirements.txt`) | ls |
| Deprecated `on_event` use | yes (`@app.on_event("shutdown")`) | `cryo/api/app.py:101` |

---

## 4. Per-attribute deep evaluation

### A. User Interface

#### A1 Visual consistency — **6/10**
Design tokens in `web/src/styles.css:4-46` (light + dark CSS vars); IBM Plex
Sans/Mono imported at line 1-2; Ant `ConfigProvider` mirrors the palette in
`web/src/App.tsx:282-340`. **Smells:**
- `Menu` palette is hard-coded inline in `App.tsx:330-338` rather than
  token-driven; sidebar colors (`#0c121b`, `#141c28`) are duplicated
  between CSS vars and TSX.
- `styles.css` is **2,535 lines** mixing global resets, page-specific
  overrides, and utility classes — drift is inevitable.
- 170 `!important` declarations in `styles.css` — Ant theme leaks.

**Fixes:** route all palette values through CSS vars consumed by
`ConfigProvider.token`; split `styles.css` into `tokens.css` + per-page
modules; remove dead duplicate vars; lint rule to ban `!important` outside
`tokens.css`.

#### A2 Information hierarchy & density — **7/10**
Heavy pages use Ant `Card`, `Tabs`, `Statistic`, Recharts; KPI stripes
(`components/table-detail/KpiStripe.tsx`) and gauges (`HealthGauge.tsx`)
give scannable entry points. **Smells:**
- `pages/TableDetailRedesign.tsx` is 2,590 LoC as a single component —
  re-render storms, overstuffed tabs.
- `pages/Connections.tsx` 2,313 LoC; `pages/CostInsights.tsx` 794;
  `pages/Scans.tsx` 779; `pages/Governance.tsx` 766.

**Fixes:** decompose TableDetail into the already-existing
`components/table-detail/*`; cap tab content <300 LoC.

#### A3 Responsive & adaptive layout — **3/10**
- `body { min-width: 1100px }` (`styles.css:63`) — **desktop-only by
  design**; layout breaks below 1100.
- Mobile nav fallback exists as a `Drawer` (`App.tsx:249-272`) but only the
  nav is hidden/re-shown; content grids still assume ≥1100.
- Three real breakpoints (`@media (max-width: 900px)` × 2, 1280px × 1) all
  collapse padding only, no reflow.
- Mobile menu button visible only because `App.tsx:200` adds
  `className="mobile-menu-btn"` (CSS at `styles.css:610-624`).

**Fixes:** remove `min-width: 1100px`; introduce responsive grid for
TableDetail/CostInsights/Connections; use `Grid.useBreakpoint()` to stack
KPI stripes vertically <768; test at 1440/1024/768/390.

#### A4 Component reuse & library fit — **7/10**
- Ant Design is the canonical library; Recharts for charts; TanStack Query
  for data; Vite + React 18.
- Custom CSS frequently overrides Ant (170 `!important`) — leaks design
  system.
- Bespoke widgets that *should* exist (PlanPreview, CostSavingsWaterfall,
  PartitionHistogram) are cleanly typed and well-scoped.

**Fixes:** audit `!important` overrides; prefer Ant `Space`/`Flex`/`Grid`;
replace ad-hoc CSS with Ant layout primitives; track Ant version upgrades.

#### A5 Loading / empty / error states — **6/10**
- Global `Suspense` fallback `Spin` (`App.tsx:59-63`) and `PageFallback`.
- Per-page coverage uneven: large pages likely have inconsistent skeletons.
- No route-level React error boundary (`grep` finds zero `componentDidCatch`
  / `getDerivedStateFromError`).
- Auth error rendered as small inline text (`App.tsx:208-211`) — easy to
  miss.

**Fixes:** top-level `<ErrorBoundary/>`; standardize skeletons via
`<QueryState/>` helper; surface auth errors as a banner; add empty
illustrations to Tables/Scans/Runs when zero rows.

### B. Accessibility (WCAG 2.1 AA target)

#### B1 Keyboard & focus — **5/10**
- `:focus-visible` ring defined (`styles.css:71-74`); Ant defaults apply.
- Sidebar `Menu` is keyboard-navigable by default; `ThemeToggle` is a
  `Button` inside `Tooltip` (`App.tsx:139-146`).
- **Missing:** no visible "Skip to main content" link.
- Modal/Drawer focus-trap behavior is library-default; not verified.
- PlanPreview/LogList/PartitionHistogram keyboard operability not verified.

**Fixes:** add skip link; verify focus trap in Drawer/Modal; add keyboard
handlers for run actions (cancel, retry).

#### B2 Semantic HTML & ARIA — **4/10**
- 15 `aria-label` and 4 `role` (3× `img`, 1× `navigation`) across 30k+ LoC
  of frontend — **sparse**.
- `App.tsx:201` adds `aria-label="Open navigation menu"` on the mobile
  button — a positive pattern not replicated.
- Landmarks come from Ant `Layout.Sider/Header/Content` (good).
- **`<h1>–<h6>` count: 0** — page headings likely use Ant `Typography.Title`
  (renders to `h*` semantically, but not grep-able here). Risk of multiple
  h1s and missing hierarchy.

**Fixes:** enforce one `<h1>` per page (visual + DOM check); add
`aria-label` to all icon-only buttons; ensure `<Table>` columns have
header scope; pair form inputs with `<label htmlFor>` in CronInput and
FilterBar.

#### B3 Color contrast & color independence — **5/10**
- Dark theme: `#e7ecf3` ink on `#0b0f16` bg → >14:1 (excellent).
- Light theme: `--ink: #131b24` on `#ffffff` → 16+:1; `--muted #5c667a`
  on white → ~6.5:1 (passes AA).
- `--ink-faint #8791a0` on white → ~3.6:1 (fails AA for body text; OK
  only ≥18px bold or decorative).
- Health grades and run statuses likely rely on color alone.

**Fixes:** raise `--ink-faint` to ≥4.5:1; add icons + text labels to
health/run status chips (color independence); verify with `axe` in CI.

#### B4 Assistive tech — **4/10**
- 15 `aria-*` total, **0 `aria-live` regions** — no polite/assertive
  announcements for run progress, scan completion, or toasts.
- Custom tooltips on truncated table cells may be hidden from screen
  readers.
- No `aria-describedby` chains anywhere.

**Fixes:** add global `<NotificationRegion aria-live="polite"/>`; ensure
toasts use Ant's accessible defaults; add `aria-describedby` to filter
chips and form fields; describe chart contents for SR.

#### B5 Motion & cognitive load — **7/10**
- `prefers-reduced-motion` respected at `styles.css:551, 726` (good).
- Heavy pages risk cognitive overload (TableDetail, CostInsights,
  Connections) — multiple tabs, charts, tables per page.
- No infinite animation; Ant defaults are short.

**Fixes:** "compact mode" toggle for dense pages; collapse secondary
panels by default; debounce table-row hover effects.

### C. Usability

#### C1 Onboarding & first-run clarity — **4/10**
- No first-run wizard or checklist. `Intelligence`, `Governance`,
  `CostInsights` likely empty until scans complete.
- `scripts/` (per AGENTS.md) for seeding — no in-app affordance.
- No "no data" CTA on the Tables explorer when zero sources.

**Fixes:** onboarding banner when `sources == 0`; link to `make seed` or
in-app "Create demo source"; empty-state CTAs on every list page.

#### C2 Navigation & wayfinding — **6/10**
- 14 top-level routes grouped into 5 groups (`App.tsx:100-132`) — clear
  IA.
- 212px fixed `Sider` (`App.tsx:186`) — wastes space on small screens;
  collapsible only via Drawer on mobile.
- No breadcrumbs; user loses context on deep pages (`/tables/:id`,
  `/runs/:runId`).
- Duplicate icons: two `DatabaseOutlined`, two `DollarOutlined`
  (`App.tsx:81, 82, 87, 93`).
- Route alias `routeSelectionAliases` (`App.tsx:134`) maps `/jobs → /runs`
  — naming inconsistency surfaced.

**Fixes:** add breadcrumbs + page titles (Ant `PageHeader` or
`Typography.Title`); deduplicate icons; persist `Sider` collapsed state.

#### C3 Discoverability & affordances — **6/10**
- Ant `Tooltip` used (theme toggle, mobile menu); advanced options often
  hidden behind toggles in PlanPreview.
- Filters have `FilterBar.tsx` + chips (`lib/filterChips.ts`) — well
  engineered.
- Bulk actions supported (`BulkAssignBar.tsx`) — positive.
- Cron and predicate helpers (`CronInput`, `lib/predicate.ts`) are
  self-contained.

**Fixes:** surface keyboard shortcuts (`/` to focus filter); inline help
for cron and predicates; command palette (`Cmd-K`) for power users.

#### C4 Task completion efficiency — **7/10**
- Plan preview (`components/PlanPreview.tsx`), run console
  (`RunConsole.tsx`), live event hook (`hooks/useRunLiveEvents.ts`) —
  strong feedback loop.
- Bulk selection ships a filter spec (per AGENTS.md §12/§13) — good.
- Cost preview (`cryo/api/preview_cost.py`) before destructive ops —
  strong.

**Fixes:** "dry-run by default" toggle in `Presets`; clone-run from
history; persist last-used filters in URL (deep linkability).

#### C5 Feedback & observability for users — **7/10**
- `useRunLiveEvents` streaming; `LogList.tsx` renders; deep links work.
- Audit log model (referenced via `extensions.py:33`) — backend solid; UI
  exposure unknown.
- `components/RunConsole.tsx` (211 LoC) and `LogList.tsx` (111 LoC) are
  the surface for run feedback.

**Fixes:** show "last maintenance / last scan" timestamps on table cards;
inline planner decision rationale in PlanPreview; success/failure toast
on every mutation.

### D. Engineering

#### D1 Modularity & file hygiene — **3/10**
**Top monolithic files:**

| File | LoC | Role |
|---|---:|---|
| `cryo/api/extensions.py` | **2,352** | 47 routes — AI/copilot/MCP/lineage/quality/FinOps grab-bag |
| `cryo/api/routes.py` | **1,990** | 61 routes — core CRUD |
| `cryo/api/advanced_routes.py` | **1,228** | 25 routes — lineage, anomalies, approvals |
| `cryo/db/models.py` | **1,182** | All ORM models in one file |
| `cryo/api/schemas.py` | **1,065** | All Pydantic schemas |
| `cryo/api/dashboard_routes.py` | **786** | Dashboards |
| `web/src/api/openapi.d.ts` | **5,024** | Generated (acceptable) |
| `web/src/pages/TableDetailRedesign.tsx` | **2,590** | One component, the entire detail UI |
| `web/src/pages/Connections.tsx` | **2,313** | One page |
| `web/src/styles.css` | **2,535** | Global + per-page CSS |
| `web/src/api/client.ts` | **1,687** | Generated-style API client |

**Smells:**
- `pages/TableDetail.tsx` is a 1-line re-export shim
  (`export { TableDetail } from "./TableDetailRedesign";`) — indicates
  WIP, rename-in-progress.
- `cryo/` has 40+ top-level subpackages with overlapping semantics
  (`compliance` vs `governance` vs `quality` vs `signals`,
  `cost` vs `finops`, `optimizer` vs `planner` vs `engines`).
- 6 backend files >1,000 LoC; 1 frontend page >2,000 LoC.

**Fixes:** split each >500 LoC module by domain; add ruff/pylint cap
(`max-line-length` + per-file LoC); decompose TableDetailRedesign into
per-tab containers (the directory `components/table-detail/` already
exists with 9 files — they are not all wired in); audit
`cryo/` subpackage boundaries.

#### D2 Type safety & contracts — **7/10**
- Pydantic v2 schemas (`cryo/api/schemas.py`, 1,065 LoC) — comprehensive.
- OpenAPI tags well-defined (`cryo/api/app.py:33-58`).
- TS strict mode presumed (`web/tsconfig.json`); `openapi.d.ts` (5,024
  LoC) suggests generated types — good.
- **Risks:** `JsonDict` ("catch-all" pydantic type) and `Any` leakage in
  `extensions.py` and `routes.py` reduce contract rigor; some routes
  likely accept untyped dicts.

**Fixes:** replace `JsonDict` with concrete models per endpoint; enforce
`additionalProperties: false`; fail CI on untyped route handlers.

#### D3 Testing depth — **6/10**
- 75 backend test files; integration suite under `tests/integration/`.
- Heavy naming (`test_phase9_ai.py`, `test_phase8.py`,
  `test_premortem_hardening.py`) — phase-driven, not feature-driven
  organization.
- Frontend: 21 test files for 30+ components/pages (1.4 tests per file,
  with some near the 2,000-line pages). E.g. `pages/TableDetailRedesign.tsx`
  has no direct test file.
- E2E: `web/e2e/smoke.spec.ts` only.
- Parity tests for SQL renderers exist (per AGENTS.md §4) — good.

**Fixes:** Vitest coverage for top 5 pages; expand Playwright to
critical paths (create source → scan → preview plan → run → view logs);
coverage thresholds in CI; reorganize backend tests by feature, not
phase.

#### D4 Performance & scalability — **6/10**
- TanStack Query 15s `staleTime` (`App.tsx:65-72`) — sensible.
- Lazy routes via `React.lazy` (`App.tsx:29-57`) — good code-splitting.
- WS streaming for run events (`hooks/useRunLiveEvents.ts`) — strong.
- **Virtualization: not used** — large tables (Tables, Runs, Scans) may
  render thousands of rows (no `react-window`/`@tanstack/react-virtual`
  found in grep).
- Async where it pays (API, governor) per AGENTS.md; scanner/worker
  sync is justified.
- Leader-election and per-table locks per AGENTS.md §1 — strong.

**Fixes:** enable Ant `Table` virtualization for inventory; server-side
pagination on `/api/v1/tables`; cache stats with short TTL; profile
React DevTools for the TableDetail page.

#### D5 Security posture — **8/10**
- `auth_routes.py`, `security.py`, `redact.py`, `crypto.py` (encrypt
  secrets) — layered.
- RBAC via `require_admin/operator/editor/viewer` (`cryo/api/deps.py`).
- CORS + rate limiting (`cryo/api/app.py:61-75`).
- Correlation middleware + audit log present.
- `SECURITY.md` and `.gitleaks.toml` at repo root — good.
- Risk: rate limiting disabled in tests (`enabled=settings.environment
  != "test"`) — verify it's enabled in staging.

**Fixes:** enforce auth on WS endpoints; CSRF for cookie-based
mutations; secret rotation policy; SBOM and dependency scanning in CI.

#### D6 Documentation & discoverability — **8/10**
- `README.md`, `plan.md`, `hld.md`, `lld.md`, `agents.md`, 40+ files in
  `docs/` (premortems, ADRs, runbooks, roadmaps) — extensive.
- `docs/LOCAL_DEV_ONLY_INVENTORY.md` ensures dev-only stuff is tracked
  — exemplary.
- OpenAPI tagged.
- Risk: docs may be stale given code churn (`TableDetail.tsx` vs
  `TableDetailRedesign.tsx`).

**Fixes:** docs freshness check; remove or merge superseded files; link
ADRs from code; index docs in `docs/README.md` (currently unindexed —
this report begins to fix that).

#### D7 Operational readiness — **8/10**
- `/healthz` (`cryo/api/app.py:78-80`), Prometheus metrics
  (`metrics_routes.py`), OTel tracing (`instrument_fastapi`), graceful
  shutdown via `@app.on_event("shutdown")`.
- Helm chart at `deploy/helm/cryo/`.
- Leader-elected scheduler/governor.
- Three docker-compose files: prod-shaped, datalake, observability.

**Fixes:** migrate `on_event` → `lifespan`; add readiness vs liveness
split; document leader failover behavior; add SLO definitions in
`docs/`.

### E. Experience

#### E1 End-to-end coherence — **5/10**
- **Mixed naming:**
  - `runs` vs `jobs` (alias map `App.tsx:134`; route still mounted at
    `/jobs/:jobId` → `JobDetail` at `App.tsx:240`).
  - `LayoutOptimization` page vs `/optimize` route (`App.tsx:89, 236`).
  - `MultiCatalog` page vs `/catalogs` route (`App.tsx:81, 225`).
  - `pages/CostInsights.tsx` vs `pages/CostAttribution.tsx` vs
    `pages/CostSavings.tsx` — three cost pages, easy to confuse.
- Backend `presets`, `maintenance plans`, `operations` used
  interchangeably in `lld.md` / `plan.md`.
- UI labels and doc terms should be reconciled into one glossary.

**Fixes:** `docs/GLOSSARY.md`; rename `LayoutOptimization` →
`Optimize`; collapse `jobs` → `runs` (deprecate alias); unify cost page
naming.

#### E2 Trust & transparency — **7/10**
- `force` flag and guardrails on destructive ops (per AGENTS.md §7).
- Cost preview before runs (`cryo/api/preview_cost.py`).
- Audit logging on every action.
- PlanPreview renders before execution.

**Fixes:** surface "what will this change?" diff in plan preview; add
"explain this SQL" affordance (Trino vs Spark); show planner rationale
inline (already partially in PlanPreview).

#### E3 Developer/operator experience — **7/10**
- `Makefile`, `start.sh`, `start-local.sh`, three docker-compose files
  — comprehensive.
- `make up / up-cryo / up-datalake / seed` (per AGENTS.md §6).
- **Triple dep manifests:** `pyproject.toml` (1,699 B), `Pipfile`
  (335 B), `requirements.txt` (133 B) — drift risk.
- `.env.example` present.
- `.impeccable`, `.kilo`, `.cursor` config dirs — three AI tool configs.

**Fixes:** pick one (uv + pyproject); remove `Pipfile`/`requirements.txt`
or document why both exist; consolidate AI tool configs.

---

## 5. Score summary

| Lens | Attributes | Avg | Weight | Weighted |
|------|-----------:|----:|-------:|---------:|
| A. UI | 5 | 5.8 | 0.15 | 0.87 |
| B. Accessibility | 5 | 5.0 | 0.10 | 0.50 |
| C. Usability | 5 | 6.0 | 0.20 | 1.20 |
| D. Engineering | 7 | 6.4 | 0.35 | 2.24 |
| E. Experience | 3 | 6.3 | 0.20 | 1.26 |
| **Overall** | **25** | — | — | **~121 / 200** (60.7%) |

**Letter grade:** **C+** — functional and ambitious, with strong
documentation and security foundations, but held back by monolithic
files, desktop-only layout, sparse accessibility, and naming
inconsistencies.

---

## 6. Top issues, prioritized

1. **Decompose monoliths.** Split `cryo/api/extensions.py` (2,352),
   `routes.py` (1,990), `advanced_routes.py` (1,228); split
   `pages/TableDetailRedesign.tsx` (2,590) and `pages/Connections.tsx`
   (2,313). Add lint rule: ≤500 LoC/file.
2. **Make the UI responsive.** Remove `body { min-width: 1100px }`;
   reflow TableDetail/CostInsights/Connections below 1100; test at
   1440/1024/768/390.
3. **Accessibility baseline.** Add skip link, label all icon buttons,
   single `<h1>` per page, `aria-live` for run/notification regions,
   status chips with icon+text redundancy, raise `--ink-faint` contrast.
4. **Empty/error/loading consistency.** Top-level error boundary,
   `<QueryState/>` helper, onboarding banner for empty sources.
5. **Glossary + naming coherence.** Reconcile `runs/jobs`,
   `LayoutOptimization/Optimize`, `MultiCatalog/Catalogs`. Deprecate
   route aliases.
6. **Test depth.** Vitest for top 5 pages, Playwright critical paths,
   parity tests verified in CI.
7. **FastAPI lifespan migration.** Replace deprecated `on_event` with
   `lifespan` (`cryo/api/app.py:101`).
8. **Dep source hygiene.** Choose `pyproject.toml` (+ uv) or `Pipfile`
   exclusively; remove or document the others.
9. **Virtualization + server pagination.** Inventory tables for >10k
   rows.
10. **Docs index.** Make `docs/README.md` a real index (this report
    begins that effort).

---

## 7. Validation plan (for future implementers)

- `make lint && make typecheck && make test` green.
- `axe-core` via Playwright: 0 serious/critical violations on each route.
- Lighthouse mobile ≥85, desktop ≥95 for `/health`, `/tables`, `/tables/:id`.
- `cloc` report: no file >500 LoC after refactor.
- OpenAPI client (`web/src/api/openapi.d.ts`) regenerates with zero
  hand edits.
- E2E spec covers: create source → scan → preview plan → run → view
  logs.

---

## 8. Out of scope (explicit)

- Performance benchmarking under load (no runtime data here).
- Security penetration testing.
- Visual design rebranding (only consistency/coherence, not new identity).
- Backend feature additions beyond the parity checklist in
  `AGENTS.md §5`.

---

## 9. Re-rating cadence

Re-rate quarterly, or after any of:

- A new public release of Cryo.
- Decomposition of any file >1,000 LoC.
- A major UI redesign.
- An accessibility audit by an external reviewer.

Store the new report as `docs/RATING_YYYY-MM-DD.md` and link from
`docs/README.md`.
