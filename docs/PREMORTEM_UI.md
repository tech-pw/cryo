# Cryo Premortem, Part 3 — The UI, 6 Months Out

Companion to `docs/PREMORTEM.md` (infra/auth/deployment) and `docs/PREMORTEM_FEATURES.md`
(backend feature-by-feature). This pass covers the React/TypeScript frontend
(`web/src/pages/`, `web/src/components/`) specifically: what's broken, what's built on the
backend but never surfaced, what's confusing, and what will visibly degrade at real usage. Every
finding cites a real file/line. Actionables are appended to `docs/PREMORTEM_ACTIONABLES.md`.

---

## Remediation status (2026-07-05 UI hardening pass)

| Section | Original risk | Status | Fix / notes |
|---|---|---|---|
| Destructive confirmations | None anywhere | **Fixed** | `ConfirmAction` on bulk run, table run, deletes, cancel, AWS remove |
| Auth | Role string as bearer token | **Fixed** | Login page + JWT exchange; session stored; sign-out |
| Error → empty state | 4 dashboard pages | **Fixed** | `QueryErrorAlert` on MultiCatalog, CostAttribution, QueryPerformance, SLAStatus, JobDetail |
| Pagination / truncation | 500-table silent cap | **Fixed** | `top_limit=50` on dashboard API; `TruncatedResultsBanner`; Runs server pagination |
| Dead backend UI | tiering/approvals/lineage/etc. | **Fixed** | New `/governance` page surfaces advanced + phase8 APIs |
| Hand-rolled fetch | 5 pages bypass client | **Fixed** | All use `client.ts` / `dashboardFetch` + `ApiError` |
| Formatting drift | Local color/currency maps | **Fixed** | Shared `lib/format`, `status.tsx`, `PageHeader` on newer pages |
| Orphan `/jobs/:id` | Never linked | **Fixed** | `RunConsole` links to job detail |
| Page tests | Zero page coverage | **Partial** | `DatalakeHealth.test.tsx`, `Login.test.tsx`, `confirm.test.tsx` added |
| Dashboard stale after mutation | Missing invalidation | **Fixed** | Table detail run/scan invalidates `dashboard` + `cost-dashboard` keys |
| RunConsole logs | JSON metadata blob | **Fixed** | Drawer shows status, error, filtered live events + link to full detail |
| FilterBar a11y | Placeholder-only labels | **Fixed** | `aria-label` on grade/priority selects |
| First-run empty state | Ambiguous copy | **Fixed** | `FirstRunEmptyState` with link to `/connections` |
| 401/403 UX | Generic errors only | **Fixed** | `QueryErrorAlert` detects auth errors with guidance |
| Responsive stat grids | Fixed `span={4-6}` | **Partial** | `xs/sm/md` breakpoints on MultiCatalog, QueryPerformance, CostAttribution |
| Production SSO | Dev token endpoint | **Open** | Documented on login screen; prod must disable `/auth/token` |

---

## The single scariest finding

**There is no destructive-action confirmation anywhere in the entire app.** Not one
`Popconfirm`/`Modal.confirm` exists in `web/src` (verified by grep across the whole tree). Deleting
an AI provider or Slack config fires the mutation directly on click
(`Connections.tsx:370,478`). Running a maintenance preset against a table fires on click
(`TableDetail.tsx:186`). Worst of all: the fleet-wide bulk-assign bar's "Run now" button
(`BulkAssignBar.tsx:121-129`) can trigger real maintenance jobs across **every table matching the
current filter**, with the same single click and zero confirmation step. Six months out, this is
the difference between "we caught a mistake before it ran" and "someone fat-fingered a filter and
kicked off jobs against 4,000 tables before anyone could stop it."

**Close second:** there is no real authentication in the frontend at all. `AuthProvider`
(`src/lib/auth.tsx:1-38`) is a `<select>` dropdown holding `"viewer"|"operator"|"admin"`, persisted
to `localStorage`, sent verbatim as `Authorization: Bearer <role-string>` on every request
(`client.ts:79-97`) and even in the WebSocket URL (`client.ts:271-276`). This isn't just "the
backend's JWT verification is broken" (already known from `docs/PREMORTEM.md`) — the frontend was
never built with a login flow to begin with. A user can open the role dropdown, or just edit
`localStorage`, and grant themselves admin on a tool that can delete snapshots and orphan files.
Nothing in the UI signals that this "admin" selector is decorative.

---

## 1. Dead backend features / dead frontend code

A meaningful amount of backend work has **no UI surface at all**: the entire tiering feature
(`cryo/api/phase8_routes.py` — tier policies CRUD, tier candidates, `/routing/preview`) has zero
frontend callers; `dashboard_routes.py`'s `/s3-tiering-recommendations` is unconsumed; and
`advanced_routes.py`'s approvals, lineage graph, anomaly list, and FinOps forecast endpoints
(~500 lines of backend code) have no page or component referencing them anywhere. Going the other
direction, `getSchemaCost` (`client.ts:262-269`) is exported and typed but never called — a
schema-cost breakdown built on the client side with nowhere to render it. Five pages
(`MultiCatalog.tsx:74`, `QueryPerformance.tsx:76`, `CostAttribution.tsx:61`, `SLAStatus.tsx:87`,
`JobDetail.tsx:71`) bypass the shared `client.ts`/`ApiError` handling entirely and hand-roll their
own `fetch("/api/...")` calls — they happen to work today because the path prefix matches, but
they duplicate auth-header logic outside the one place error handling is centralized, and will
silently break the moment someone "fixes" the API prefix to be consistent everywhere else.

## 2. Forms and mutations

Field validation and per-mutation loading states are actually solid across Connections/Presets/
AwsIntegrationPanel. The gap is entirely the confirmation-dialog issue above — every other
form-UX basic (validation, loading spinners, success/error toasts) is present.

## 3. Errors silently rendered as empty states

`MultiCatalog.tsx`, `CostAttribution.tsx`, `QueryPerformance.tsx`, and `SLAStatus.tsx` all
destructure only `{ data, isLoading }` from their `useQuery` calls — never `error`/`isError`. On a
fetch failure, `isLoading` becomes `false`, `data` stays `undefined`, and the page renders
`data?.total_catalogs || 0` (`MultiCatalog.tsx:210`) plus an empty table — **visually
indistinguishable from "zero catalogs genuinely configured."** `JobDetail.tsx:88-97` is the one
page that does this correctly, with a real `<Alert type="error">`. A user hitting an outage on any
of the other four pages will conclude the fleet is empty, not that something is broken.

## 4. Scale UX

`DatalakeHealth.tsx:30` — the app's default landing page — hardcodes `listTables(api, { limit:
500, offset: 0 })` and renders with `pagination={false}` (`:149`): past 500 tables, offenders fall
off the list silently, with no "load more," no count of how many are hidden. `Runs.tsx:28` fetches
`listRuns(api)` with no limit/offset at all, relying entirely on client-side pagination over
whatever the API happens to return unbounded. `TableExplorer.tsx`, by contrast, does real
server-side pagination correctly (`pageSize=50`) — the pattern exists in the codebase, it just
wasn't applied consistently to the pages most likely to be viewed by an operator with a large fleet.

## 5. Navigation

All 13 nav items map cleanly to routes, and every route is otherwise reachable — except
`/jobs/:jobId` (`App.tsx:155`, backing `JobDetail.tsx`), which is **never linked from anywhere in
the app**. It's a fully orphaned page, reachable only by someone typing the URL directly or
following a link from outside the app (e.g., a Slack report link) — a page that was clearly built
to be linked *to* from somewhere, but the "from somewhere" was never wired.

## 6. Visual/formatting consistency drift

`components/status.tsx` (grade/status/priority tags) and `common.tsx` (page header, empty state)
are used consistently by the app's older pages (Connections, Presets, TableDetail, TableExplorer,
DatalakeHealth, Runs, Scans, CostSavings). But every newer dashboard page — MultiCatalog,
CostAttribution, QueryPerformance, SLAStatus, JobDetail — imports neither shared module and
reinvents its own color map, currency formatter, and status-label logic locally (e.g.,
`MultiCatalog.tsx:49-58`'s `getHealthColor`, `JobDetail.tsx:49`'s `statusConfig`,
`CostAttribution.tsx:49`'s `COLORS`). Six months from now, an operator moving between the "old"
and "new" parts of the app will see the same health grade or the same dollar figure rendered with
different colors, different decimal precision, or different terminology — a small thing per page,
compounding into "this product feels like it was built by two different teams that didn't talk."

## 7. Frontend test coverage

Exactly 8 test files exist, all Vitest + Testing Library at the isolated hook/small-component
level (`BulkAssignBar.test.tsx`, `FilterBar.test.tsx`, plus `cost`/`cron`/`format`/`predicate`/
`tableSelection` unit tests). **None render a full page, and there is no Playwright/Cypress/e2e
tooling anywhere in `package.json`.** Every page file — including the largest and most complex,
`Connections.tsx` at 65KB — has zero test coverage. A regression that breaks `App.tsx` routing, or
a runtime crash on mount in any page, would ship to production completely unnoticed by CI; nothing
exercises "does this page render, fetch data, and respond to a click" for any of the 15 pages.

## 8. Auth UX (distinct from the "no real auth" finding above)

There is no login page, no session concept, and no 401/403 handling anywhere in the frontend
(`grep` for `401|403|Unauthorized|Forbidden` across `src` returns nothing beyond generic error
plumbing). Every mutation's error handler is the same generic `message.error((error as
Error).message)` at 30+ call sites — a permission-denied response looks, sounds, and reads
identically to a network blip or a validation error, with no "please log in" or "you don't have
permission" affordance and no redirect. The one place role-gating exists client-side
(`Scans.tsx:41`, `canOperate = token !== "viewer"`) is trivially defeated by switching the same
dropdown that grants the role in the first place.

## 9. Real-time / long-running job UX — a genuine strength, with one gap

This is the best-built part of the frontend: `RunConsole.tsx` opens a real WebSocket for live job
events with an automatic polling fallback and a visible "Live socket unavailable; polling every 5
seconds" alert if the socket drops; `ScanConsole.tsx` computes a real percent-complete bar from
actual assigned/scanned/skipped/failed counts, not a generic spinner, and surfaces per-table
failure reasons in text, not just a red badge. The one weak spot: the "Logs" button in
`RunConsole.tsx:144-166` opens a drawer that just `JSON.stringify`s the job record — a user
expecting to see actual execution output for a stuck or failed job gets a metadata blob instead of
logs.

## 10. Cross-page number consistency

Every dashboard page queries independently via React Query with a global 15-second `staleTime`
and its own query key. Cache invalidation after mutations is inconsistent:
`TableDetail.tsx:127-130` invalidates table/scan-specific keys after an action but never
invalidates the dashboard or cost-dashboard keys, so `DatalakeHealth`/`CostSavings` can keep
showing stale fleet-wide totals for 15+ seconds after a change made from a table's own detail page.
Worse, `DatalakeHealth.tsx` has its own client-side `deriveDashboard` fallback that recomputes
totals from raw `/tables` data whenever the real dashboard endpoint errors — **two independent
aggregation code paths for the same "current cost"/"tables needing attention" number**, which can
diverge from what the server-side dashboard endpoint would compute, with no indication to the user
that they're looking at the fallback calculation rather than the real one.

## 11. Responsive design

Mixed and inconsistent, not absent: some pages use real Ant Design breakpoints
(`LayoutOptimization.tsx`, `Intelligence.tsx`, `MultiCatalog.tsx`'s detail sections), but the
summary stat-row grids on `QueryPerformance.tsx`, `CostAttribution.tsx`, and `MultiCatalog.tsx`'s
top row all use fixed `Col span={4-6}` with no responsive breakpoint — these will compress rather
than stack on a laptop-width or narrower screen. There's exactly one CSS breakpoint in the whole
app (`@media (max-width: 900px)`), so anything below that gets one blanket override rather than
real per-page tuning. Data-dense tables rely on horizontal scroll rather than column collapsing —
workable with a mouse, not touch-friendly.

## 12. Accessibility

Better than the average premortem finding predicts: no icon-only buttons exist anywhere (every
button carries visible text), and status tags always render the grade/status as text inside the
tag, not color alone — so color-blind users aren't locked out of reading health grades. The real
gap is narrower: `FilterBar.tsx`'s Select controls rely only on a `placeholder` for their
accessible name, which disappears once a value is selected, so a screen-reader user loses the
field's label at exactly the point a filter is active. Table rows with click-to-navigate handlers
also have a real `<Link>` in the first column, so keyboard access is preserved even though it looks
like an accidental side effect rather than an intentional design choice.

## 13. Onboarding / first-run experience

Decently handled where it exists: `Connections.tsx` has clear, actionable empty states ("No
lakehouses connected — Connect a catalog to start scanning Iceberg tables"). The gap is on
`DatalakeHealth.tsx` — the app's default landing route for every new deployment — where the
zero-tables empty state reads "No offender tables — The datalake is currently quiet." That copy is
**ambiguous between "nothing is configured yet" and "everything is healthy,"** and a first-time
user landing on an all-zero dashboard with reassuring-sounding text has no explicit prompt to go
set up a connection first.

---

## So — how will the UI actually work, 6 months out?

For an operator who already trusts the tool, sticks to the older/core pages (health, tables, cost,
connections, scans, runs), and never hits an error, the day-to-day experience will feel solid — the
real-time job console is genuinely well-built, forms validate properly, and the core pages share a
consistent design language.

Where it degrades:

- **The first serious mistake will be a UI mistake, not a backend one.** With zero confirmation
  dialogs anywhere, the most dangerous action in the whole product — bulk-running maintenance
  across a filtered set of tables — is one accidental click away from a real incident, and there's
  no client-side safety net to compensate for the backend's own weak admission controls.
- **A meaningful fraction of what's been built on the backend this month (tiering, approvals,
  lineage, anomalies, FinOps forecasting) is invisible to users** — not broken, just never
  surfaced, so "did we ship that?" will have a confusing answer six months from now unless someone
  builds the UI or explicitly shelves the backend work.
- **The newer pages (MultiCatalog, CostAttribution, QueryPerformance, SLAStatus, JobDetail) will
  visibly diverge in look and correctness from the older core pages** — different formatting,
  silently-swallowed errors instead of visible ones, and hand-rolled fetch calls outside the shared
  error-handling path — because they were built without reusing the shared components/patterns the
  rest of the app already has.
- **The first time someone opens this on a large fleet (thousands of tables) or a laptop screen,**
  the default dashboard silently truncates at 500 tables with no indication, and several stat grids
  will visually compress rather than adapt.
- **Nothing will catch a page-level regression before a user does** — with zero page-level or
  end-to-end frontend tests, a broken route or a crash-on-mount bug ships the moment it's written,
  same risk profile as the backend's own thin end-to-end coverage noted in
  `docs/PREMORTEM_ACTIONABLES.md`.

The fix list below (`docs/PREMORTEM_ACTIONABLES.md`) prioritizes the confirmation-dialog gap and
the missing login/auth flow first — both are the UI's own version of the same "a safety mechanism
looks present but isn't enforced" pattern found throughout the backend audit.
