# Cryo Codebase Rating Plan

## Goal
Define the rating framework (attributes + scoring rubric) and apply it deeply
to the Cryo repo (self-hosted Apache Iceberg maintenance platform: FastAPI +
React/Vite/Ant Design UI, scheduler/governor/worker/scanner, planner, engine
adapters). Output: per-attribute score (0–10), key evidence, and concrete fix
list — no source edits in this plan.

---

## 1. Rating framework (attributes to rate)

Grouped into four lenses. Each rated 0–10 with a one-line definition, rubric
(0/2/5/8/10 anchors), and explicit "what to look for" checklist.

### A. User Interface (Visual + Interaction Design)
1. **Visual consistency** — design tokens, typography, spacing, color, dark mode parity.
2. **Information hierarchy & density** — KPI/metric presentation, scanability, no walls of text.
3. **Responsive & adaptive layout** — breakpoints, mobile, sidebar collapse, overflow handling.
4. **Component reuse & library fit** — Ant Design idiomatic use vs. bespoke CSS hacks.
5. **Loading / empty / error states** — skeletons, spinners, empty illustrations, error toasts.

### B. Accessibility (a11y / WCAG 2.1 AA)
6. **Keyboard navigation & focus management** — `:focus-visible`, skip links, tab order.
7. **Semantic HTML & ARIA** — landmarks, headings, labels, `aria-*` correctness.
8. **Color contrast & color independence** — text/background ratios; status not conveyed by color alone.
9. **Assistive tech support** — screen reader labels, live regions for status updates, alt text.
10. **Motion & cognitive load** — `prefers-reduced-motion`, animation duration, modals, focus traps.

### C. Usability (UX / product flow)
11. **Onboarding & first-run clarity** — empty states, sample data, "what now?" prompts.
12. **Navigation & wayfinding** — sidebar IA, breadcrumbs, deep-linking, back behavior.
13. **Discoverability & affordances** — tooltips, hints, advanced-vs-simple exposure, shortcuts.
14. **Task completion efficiency** — bulk actions, filters, presets, autosave, undo where destructive.
15. **Feedback & observability for users** — run logs, plan previews, cost impact, audit trail visibility.

### D. Engineering (code & system quality)
16. **Modularity & file hygiene** — file size, single-responsibility, no grab-bag modules.
17. **Type safety & contracts** — TS strict, Pydantic v2 schemas, OpenAPI generation parity.
18. **Testing depth** — unit/integration/e2e coverage; parity tests for SQL renderers.
19. **Performance & scalability patterns** — virtualization, query caching, async where it pays, queue design.
20. **Security posture** — auth/RBAC, secret encryption, redaction, CORS/rate limit, audit log.
21. **Documentation & discoverability** — `docs/`, ADRs, AGENTS.md parity, OpenAPI, in-code intent.
22. **Operational readiness** — healthchecks, metrics, tracing, leader-election, graceful shutdown.

### E. Experience (cross-cutting, product-level)
23. **End-to-end coherence** — do UI labels match API fields, planner concepts, and docs?
24. **Trust & transparency** — destructive-op guardrails, dry-runs, cost preview, force flags.
25. **Developer/operator experience** — local dev story (`make up`), seed data, docs, debuggability.

Total: **25 attributes**, each 0–10 → max 250. Reported as category averages
and an overall weighted score (UI 15%, a11y 10%, usability 20%, engineering
35%, experience 20%).

---

## 2. Rubric (anchors for any 0–10 score)

| Score | Anchor |
|------:|--------|
| 0–1 | Absent or misleading; actively harmful. |
| 2–3 | Scaffolding only; major gaps; many violations. |
| 4–5 | Functional but rough; some good patterns, some smells; not production-grade. |
| 6–7 | Solid; mostly consistent; a few known gaps tracked in docs. |
| 8–9 | Production-grade, idiomatic, observable; minor polish items. |
| 10   | Exemplary; documented as a model; rare. |

---

## 3. Deep evaluation (per-attribute findings)

> Evidence is read from the repo at evaluation time. File:line refs use the
> current tree. "Fix" items are recommendations for the implementation agent —
> this plan only describes them.

### A. User Interface

**A1 Visual consistency — 6/10**
- Tokens defined in `web/src/styles.css:4-46` (light + dark vars); IBM Plex Sans/Mono imported (`web/src/styles.css:1-2`).
- Ant theme mirrored in `web/src/App.tsx:282-340` (primary, radii, controlHeight).
- Inconsistencies: `Menu` palette is hard-coded inline in App.tsx rather than token-driven (`App.tsx:330-338`); sidebar colors (`#0c121b`, `#141c28`) duplicated between CSS vars and TSX.
- 2,535-line `styles.css` mixes global resets, page-specific overrides, and utility classes — likely to drift.
- Fix: route all palette values through CSS vars consumed by `ConfigProvider.token`; split `styles.css` into `tokens.css` + per-page modules; remove dead duplicate vars.

**A2 Information hierarchy & density — 7/10**
- Heavy pages (e.g., `pages/TableDetailRedesign.tsx` 2,590 lines; `pages/Connections.tsx` 2,313; `pages/CostInsights.tsx` 794) use Ant `Card`, `Tabs`, `Statistic`, Recharts — generally readable.
- KPI stripes (`components/table-detail/KpiStripe.tsx`) and gauges (`HealthGauge.tsx`) provide scannable entry points.
- Risk: 2,590-line single component for TableDetail will produce re-render storms and overstuffed tabs; needs decomposition.
- Fix: split `TableDetailRedesign.tsx` into per-tab containers (`components/table-detail/*` already exist but aren't used here); enforce tab content <300 lines each.

**A3 Responsive & adaptive layout — 3/10**
- `web/src/styles.css:63` sets `body { min-width: 1100px }` — desktop-only by design; no tablet/mobile layouts below 1100.
- Mobile fallback exists as a `Drawer` menu (`App.tsx:249-272`) but only re-shown the nav; content grids still assume ≥1100.
- Breakpoints exist at 900px and 1280px (`styles.css:614, 1280, 1670, 1688`) but mostly collapse side padding — they don't reflow.
- Fix: remove `min-width: 1100px` on `body`; introduce real responsive grid for TableDetail/CostInsights; add per-page `useBreakpoint` and stack KPIs vertically <768.

**A4 Component reuse & library fit — 7/10**
- Ant Design is the canonical library; Recharts for charts; TanStack Query for data; Vite + React 18 (verify in `web/package.json`).
- Custom CSS frequently overrides Ant (`!important` in `styles.css:82, 97, 1328, 1960` etc.) — leaks design system.
- Some bespoke widgets are warranted (PlanPreview, CostSavingsWaterfall, PartitionHistogram) and are cleanly typed.
- Fix: audit `!important` overrides; prefer Ant `theme` tokens; replace ad-hoc CSS with Ant `Space`/`Flex`/`Grid`.

**A5 Loading / empty / error states — 6/10**
- Global `Suspense` fallback `Spin` (`App.tsx:59-63`) and `PageFallback`.
- Per-page coverage is uneven: large pages (`Scans.tsx` 779, `CostInsights.tsx` 794, `Governance.tsx` 766) likely have inconsistent skeletons.
- Error boundary not visible at the route level; auth error rendered as a small inline text (`App.tsx:208-211`) — easy to miss.
- Fix: add a top-level React error boundary; standardize skeletons via a `<QueryState/>` helper; surface auth errors as a banner.

### B. Accessibility

**B1 Keyboard & focus — 5/10**
- `:focus-visible` ring defined (`styles.css:71-74`) and used via Ant defaults.
- Sidebar `Menu` is keyboard-navigable by default; `ThemeToggle` is a `Button` inside a `Tooltip` (`App.tsx:139-146`).
- Likely issues in large interactive pages (TableDetail, CostInsights): custom rows, modals from `Drawer`/`Modal` need focus trap verification.
- No visible "skip to main content" link — common a11y miss.
- Fix: add skip link; audit Drawer/Modal focus traps; ensure plan-preview & log lists are keyboard-operable.

**B2 Semantic HTML & ARIA — 4/10**
- Only 15 `aria-*` attributes and 4 `role="..."` across the entire `web/src` (`grep` result). For ~13,000 LoC of pages, this is sparse.
- Landmark elements mostly come from Ant `Layout.Sider/Header/Content` (good).
- Headings: not audited yet; risk of multiple `<h1>` per page in detail tabs.
- `web/src/App.tsx:201` adds `aria-label="Open navigation menu"` on the mobile menu button — positive pattern; not replicated elsewhere.
- Fix: enforce one `<h1>` per page; add `aria-label` to icon-only buttons (theme toggle, refresh, filter); label all `<Table>` headers via Ant's accessibility props; provide `<label htmlFor>` for form inputs (CronInput, FilterBar).

**B3 Color contrast & color independence — 5/10**
- Dark theme tokens look reasonable (`#e7ecf3` ink on `#0b0f16` bg → >14:1).
- Light theme: `--ink: #131b24` on `--bg: #ffffff` → 16+:1; `--muted #5c667a` on white → ~6.5:1 (passes AA normal text).
- `--ink-faint #8791a0` on white → ~3.6:1 (fails AA for body text; OK only for ≥18px bold or decorative).
- Health grades and run statuses likely rely on color alone; need shape/text/icon redundancy.
- Fix: raise `--ink-faint` to ≥4.5:1; add icons + text labels to health/run status chips; verify with axe in CI.

**B4 Assistive tech — 4/10**
- 15 `aria-*` total — too few for a 25-page product.
- Live regions: no `aria-live="polite"` for run progress, scan completion, or toast notifications.
- Custom tooltips on truncated table cells — text may be hidden from screen readers.
- Fix: add a global `<NotificationRegion aria-live="polite"/>`; ensure toasts use Ant's accessible defaults; label chart descriptions for SR.

**B5 Motion & cognitive load — 7/10**
- `prefers-reduced-motion` is respected (`styles.css:551, 726`).
- Heavy pages risk cognitive overload (TableDetail, CostInsights, Connections) — multiple tabs, charts, tables per page.
- No issue with infinite animation; Ant's defaults are short.
- Fix: introduce a "compact mode" toggle for dense pages; collapse secondary panels by default.

### C. Usability

**C1 Onboarding & first-run — 4/10**
- No first-run wizard or checklist observed. `Intelligence`, `Governance`, `CostInsights` likely empty until scans complete.
- `scripts/` directory exists (per AGENTS.md) for seeding but no in-app "seed demo data" affordance.
- Fix: add an onboarding banner when `sources == 0`; link to `make seed` or in-app "Create demo source".

**C2 Navigation & wayfinding — 6/10**
- 14 top-level routes grouped into 5 groups (`App.tsx:100-132`) — clear IA.
- 212px fixed `Sider` (`App.tsx:186`) — wastes space on small screens; collapsible version exists only via Drawer on mobile.
- No breadcrumbs or page-titles; user loses context on deep pages (`/tables/:id`, `/runs/:runId`).
- Some duplicate icons (two `DatabaseOutlined`, two `DollarOutlined` — `App.tsx:81, 82, 87, 93`).
- Fix: add breadcrumbs + page `<Helmet>` titles; deduplicate icons; persist `Sider` collapsed state.

**C3 Discoverability & affordances — 6/10**
- Ant `Tooltip` used (theme toggle, mobile menu button); advanced options often hidden behind "Advanced" toggles in PlanPreview.
- Filters have `FilterBar.tsx` + chips (`lib/filterChips.ts`) — well-engineered.
- Bulk actions supported (`BulkAssignBar.tsx`) — positive.
- Fix: surface keyboard shortcuts (e.g., `/` to focus filter); add inline help for cron (`CronInput.tsx`) and predicates (`lib/predicate.ts`).

**C4 Task completion efficiency — 7/10**
- Plan preview (`components/PlanPreview.tsx`), run console (`RunConsole.tsx`), live event hook (`hooks/useRunLiveEvents.ts`) — strong feedback loop.
- Bulk selection ships a filter spec (per AGENTS.md §12/§13) — good.
- Cost preview (`cryo/api/preview_cost.py`) before destructive ops — strong.
- Fix: add "dry-run by default" toggle in `Presets`; allow cloning a run from history; persist last-used filters in URL.

**C5 Feedback & observability — 7/10**
- `useRunLiveEvents` provides streaming; `LogList.tsx` renders; `runs/:runId` deep-links.
- Audit log model (`cryo/db/models.py` referenced via `extensions.py:33`) — backend solid; UI exposure unknown.
- Fix: surface "last maintenance" / "last scan" timestamps on table cards; show planner decision rationale inline (currently in PlanPreview).

### D. Engineering

**D1 Modularity & file hygiene — 3/10**
- `cryo/api/extensions.py` is **2,352 lines**, 47 route handlers across AI/copilot/MCP/lineage/quality/FinOps — classic grab-bag.
- `cryo/api/routes.py` is **1,990 lines**, 61 handlers.
- `pages/TableDetailRedesign.tsx` is **2,590 lines**, the live UI behind `pages/TableDetail.tsx` (a 1-line re-export shim).
- `pages/Connections.tsx` is **2,313 lines**.
- `cryo/api/advanced_routes.py` (1,228), `dashboard_routes.py` (786), `cost_insights_routes.py` (274), `phase8_routes.py` (288) all independently large.
- Backend module `cryo/` has 40+ top-level subpackages (`ai`, `compliance`, `cost`, `finops`, `gitops`, `governor`, `iceberg`, `lineage`, `mcp`, `ml`, `optimizer`, `quality`, `queue`, `reporting`, `scheduler`, `scanner`, `security`, `signals`, `sources`, `telemetry`, `testing`, `tiering`, `triggers`, `worker`, `workflows`, …). Many overlap semantically (e.g., `compliance` vs `governance` vs `quality` vs `signals`).
- Fix: split each >500 LoC module by domain (`routes/ai.py`, `routes/compliance.py`, etc.); cap file size lint at 500; decompose `TableDetailRedesign.tsx` into per-tab components under `components/table-detail/`.

**D2 Type safety & contracts — 7/10**
- TS strict mode (`web/tsconfig.json` assumed strict; `openapi.d.ts` suggests generated types).
- Pydantic v2 schemas in `cryo/api/schemas.py` (1,065 lines) — comprehensive.
- OpenAPI tags defined in `cryo/api/app.py:33-58` — good discovery.
- Risk: `JsonDict` ("catch-all" pydantic type) and `Any` leakage in `extensions.py` and `routes.py` reduce contract rigor.
- Fix: replace `JsonDict` with concrete models per endpoint; enforce `additionalProperties: false`; fail build on untyped route handlers.

**D3 Testing depth — 6/10**
- 75 backend test files; integration suite under `tests/integration/`. Heavy naming (`test_phase9_ai.py`, `test_phase8.py`, `test_premortem_hardening.py`) signals phase-driven, not feature-driven test organization.
- Frontend: only 8 test files for 30+ components/pages (`.test.ts(x)` near `pages/`). Coverage likely thin on heavy pages.
- E2E: `web/e2e/smoke.spec.ts` — single smoke file.
- Parity tests for SQL renderers exist (`AGENTS.md` §4) — good.
- Fix: add per-page Vitest tests for top 5 pages; expand Playwright to critical paths (create source → scan → preview plan → run); gate CI on coverage thresholds.

**D4 Performance & scalability patterns — 6/10**
- TanStack Query with 15s `staleTime` (`App.tsx:65-72`) — sensible.
- Lazy routes via `React.lazy` (`App.tsx:29-57`) — good code-splitting.
- WS streaming for run events (`hooks/useRunLiveEvents.ts`) — strong.
- Virtualization not confirmed; large tables (Tables explorer, Runs, Scans) may render thousands of rows. Verify `Table` uses `virtual` prop or react-window.
- Async where it pays (API, governor loops) per AGENTS.md; scanner/worker sync is justified.
- Governor leader-election and per-table locks per AGENTS.md §1 — strong design.
- Fix: enable Ant `Table` virtualization for inventory; add server-side pagination on `/api/v1/tables`; cache stats with short TTL.

**D5 Security posture — 8/10**
- `auth_routes.py`, `security.py`, `redact.py`, `crypto.py` (encrypt/decrypt secrets) — layered.
- RBAC via `require_admin/operator/editor/viewer` deps (`cryo/api/deps.py`) — strong.
- CORS and rate limiting configured in `app.py:61-75`.
- Correlation middleware + audit log present.
- `SECURITY.md` and `gitleaks.toml` at repo root — good.
- Risk: rate limiting disabled in tests (`enabled=settings.environment != "test"`) — verify it's also enabled in staging.
- Fix: enforce auth on WS endpoints; add CSRF for state-changing endpoints if cookie-based; rotate secrets policy.

**D6 Documentation & discoverability — 8/10**
- `README.md`, `plan.md`, `hld.md`, `lld.md`, `agents.md`, and 40+ files in `docs/` (premortems, ADRs, runbooks, roadmaps) — extensive.
- `docs/LOCAL_DEV_ONLY_INVENTORY.md` ensures dev-only stuff is tracked — exemplary.
- OpenAPI tagged (`app.py:33-58`).
- Risk: many docs may be stale given the codebase churn (`TableDetail.tsx` vs `TableDetailRedesign.tsx`).
- Fix: add a docs freshness check; remove or merge superseded files; link ADRs from code.

**D7 Operational readiness — 8/10**
- `/healthz` (`app.py:78-80`), Prometheus metrics (`metrics_routes.py`), OTel tracing (`instrument_fastapi`), graceful shutdown (`on_event("shutdown")` — note: deprecated in modern FastAPI; migrate to `lifespan`).
- Helm chart at `deploy/helm/cryo/` (per AGENTS.md §3).
- Leader-elected scheduler/governor per AGENTS.md.
- docker-compose for full stack (`docker-compose.yml`, `docker-compose.dev-datalake.yml`, `docker-compose.observability.yml`).
- Fix: migrate `on_event` → `lifespan`; add readiness vs liveness split; document leader failover behavior.

### E. Experience (cross-cutting)

**E1 End-to-end coherence — 5/10**
- Mixed naming: `runs` vs `jobs` (alias map in `App.tsx:134`), `LayoutOptimization` page vs `/optimize` route (`App.tsx:89, 236`), `MultiCatalog` vs `/catalogs`.
- Backend `presets`, `maintenance plans`, `operations` used interchangeably in docs (`lld.md`, `plan.md`).
- UI labels and doc terms should be reconciled into one glossary.
- Fix: build a `docs/GLOSSARY.md`; rename `LayoutOptimization` → `Optimize` consistently; collapse `jobs` → `runs` (deprecate alias).

**E2 Trust & transparency — 7/10**
- `force` flag and guardrails on destructive ops (per AGENTS.md §7).
- Cost preview before runs (`preview_cost.py`).
- Audit logging on every action.
- Fix: surface "what will this change?" diff in plan preview; add "explain this SQL" affordance.

**E3 Developer/operator experience — 7/10**
- `Makefile`, `start.sh`, `start-local.sh`, three docker-compose files — comprehensive.
- `make up / up-cryo / up-datalake / seed` (per AGENTS.md §6).
- `pyproject.toml`, `Pipfile`, `requirements.txt` — triple-defined dep sources (potential drift).
- Fix: pick one (uv + pyproject); remove Pipfile/lock or document why both.

---

## 4. Score summary (estimated from inspection)

| Lens | Attrs | Avg | Weighted |
|------|------:|----:|---------:|
| UI | 5 | 5.8 | 0.15 × 5.8 = 0.87 |
| Accessibility | 5 | 5.0 | 0.10 × 5.0 = 0.50 |
| Usability | 5 | 6.0 | 0.20 × 6.0 = 1.20 |
| Engineering | 7 | 6.6 | 0.35 × 6.6 = 2.31 |
| Experience | 3 | 6.3 | 0.20 × 6.3 = 1.26 |
| **Overall (×20)** | **25** | — | **~123 / 200** |

> The above is a planning estimate from file-level inspection, not a
> runtime measurement. The implementation agent should refine with axe/LH
> scores, Vitest coverage, and `ruff/mypy/pytest` green signals.

---

## 5. Top issues to address (priority order)

1. **Decompose monoliths.** Split `cryo/api/extensions.py` (2,352), `routes.py` (1,990), `advanced_routes.py` (1,228); split `pages/TableDetailRedesign.tsx` (2,590) and `pages/Connections.tsx` (2,313). Add lint rule: ≤500 LoC/file.
2. **Make the UI responsive.** Remove `body { min-width: 1100px }`; reflow TableDetail/CostInsights/Connections below 1100; test at 1440/1024/768/390.
3. **Accessibility baseline.** Add skip link, label all icon buttons, single `<h1>` per page, `aria-live` for run/notification regions, status chips with icon+text redundancy, raise `--ink-faint` contrast.
4. **Empty/error/loading consistency.** Top-level error boundary, `<QueryState/>` helper, onboarding banner for empty sources.
5. **Glossary + naming coherence.** Reconcile `runs/jobs`, `LayoutOptimization/Optimize`, `MultiCatalog/Catalogs`. Deprecate route aliases.
6. **Test depth.** Vitest for top 5 pages, Playwright critical paths, parity tests verified in CI.
7. **FastAPI lifespan migration.** Replace deprecated `on_event` with `lifespan`.
8. **Dep source hygiene.** Choose `pyproject.toml` (+ uv) or `Pipfile` exclusively.
9. **Docs freshness check.** Cross-link ADRs, remove superseded files.
10. **Virtualization + server pagination.** Inventory tables for >10k rows.

---

## 6. Validation plan (for the implementer)

- `make lint && make typecheck && make test` green.
- `axe-core` (via Playwright) returns 0 serious/critical violations on each route.
- Lighthouse mobile score ≥85, desktop ≥95 for `/health`, `/tables`, `/tables/:id`.
- `cloc` report shows no file >500 LoC after refactor.
- OpenAPI client (`web/src/api/openapi.d.ts`) regenerates with zero hand edits.
- E2E spec covers: create source → scan → preview plan → run → view logs.

---

## 7. Out of scope (explicit)

- Performance benchmarking under load (no runtime data here).
- Security penetration testing.
- Visual design rebranding (only consistency/coherence, not new identity).
- Backend feature additions beyond the parity checklist in `AGENTS.md` §5.

---

## 8. Open question for user

One decision needed before finalizing:

**Q:** Should the rating be **delivered as a written report only** (this plan),
or also **bundled as a `docs/RATING_2026.md`** so future agents find it in the
project brain? (Recommended: create `docs/RATING_2026.md` and link it from
`docs/README.md`; AGENTS.md §2 lists `docs/` as the persistent project brain.)