# Cryo — UI/UX Design Documentation

> Written 2026-07-03. Grounded in the actual implementation in `web/src/` (7 pages, AntD 6 +
> Recharts + TanStack Query, existing `cryoColors` tokens in `web/src/lib/theme.ts`, existing
> `ConfigProvider` wiring in `web/src/App.tsx`) — this document **elevates** what's already built,
> it does not propose a rewrite. Every page, field, column, and button listed below is real,
> read directly from the current source, not invented.

---

## 0. Audience and north star

**Primary user: a data engineer or platform/SRE-adjacent operator**, usually mid-incident or
mid-triage — checking "which tables need attention," deciding whether it's safe to run
maintenance, or verifying a run didn't fail. They are not a casual consumer user. They:

- Scan dense tables and dashboards for outliers, they don't read prose.
- Trust numbers over adjectives — a specific $/month beats "significant savings."
- Need to act on many rows at once (bulk-select → assign → preview → run), not one at a time.
- Live in this tool during an incident (small-file blowup, cost spike) and check it passively the
  rest of the time — both modes have to work well.
- Will judge trustworthiness partly by restraint: a data tool that looks like a marketing site
  reads as less credible for production use, not more.

**North star:** feel like the internal tool a data platform team at a company with real design
standards would build for itself — closer to **Linear, Vercel's dashboard, Datadog, or Snowflake's
Snowsight** than to a consumer product. Premium reads here as *precision*, not decoration:
consistent spacing rhythm, real typographic hierarchy, restrained color spent only on meaning
(health/status), fast perceived performance, and empty/error states that were clearly designed on
purpose rather than left as a framework default.

**Explicitly avoid:** hero banners, marketing copy, gratuitous gradients or drop shadows, more than
one accent color competing for attention, mixed icon styles, centered layouts for data-dense
screens, decorative illustrations beyond the one simple empty-state graphic already in use.

---

## 1. Design tokens

### 1.1 Color — light theme (main content)

Builds on the existing `cryoColors` in `web/src/lib/theme.ts`; values marked *(unchanged)* are
already correct and should not be churned for their own sake.

| Token | Hex | Use |
|---|---|---|
| `bg` | `#FFFFFF` *(unchanged)* | Page/card canvas |
| `layoutBg` | `#F3F5F8` *(unchanged)* | Gutter behind cards, `Layout.Content` background |
| `surfaceSunken` | `#F6F8FB` | Table header row, input backgrounds, code blocks |
| `border` | `#E1E6ED` | Hairline dividers, table borders |
| `borderStrong` | `#C7D0DC` | Interactive control borders, dividers needing more contrast |
| `ink` | `#131B24` | Primary text |
| `inkSoft` | `#545F6D` | Secondary text, labels |
| `inkFaint` | `#8791A0` | Tertiary text, placeholder, disabled |
| `primary` | `#3D6FD4` *(unchanged)* | Brand accent — links, primary buttons, active nav, Grade B |
| `primaryHover` | `#3566C8` *(unchanged)* | Hover/active state of primary |
| `primarySoft` | `#E8EFFC` *(unchanged)* | Selected-row tint, info banners, active nav item background |
| `focusRing` | `#3D6FD4` at 35% opacity, 2px offset | Keyboard focus outline on every interactive element |

### 1.2 Color — dark theme (new)

Cryo has no dark mode today. Add one — most of this audience runs ops tools dark by default
(compare Datadog, Grafana, Snowsight all defaulting or offering dark). Token-level, not a naive
invert:

| Token | Hex | Use |
|---|---|---|
| `bg` | `#0B0F16` | Page/card canvas |
| `layoutBg` | `#0B0F16` | Same as bg — contrast comes from the sidebar, not the content gutter |
| `surfaceSunken` | `#0E141F` | Table header, input backgrounds, code blocks |
| `border` | `#202A38` | Hairline dividers |
| `borderStrong` | `#2E3B4C` | Interactive borders |
| `ink` | `#E7ECF3` | Primary text |
| `inkSoft` | `#A6B2C0` | Secondary text |
| `inkFaint` | `#6E7A8A` | Tertiary text |
| `primary` | `#5B9CE8` | Brightened for AA contrast on dark backgrounds |
| `primaryHover` | `#6FAAF0` | |
| `primarySoft` | `#16283F` | |
| `focusRing` | `#5B9CE8` at 40% | |

### 1.3 The sidebar is always dark, in both themes

**Decision:** the left navigation rail stays a fixed dark surface (`#0C121B`, text `#C7D2DE`,
active text `#FFFFFF`, active item gets a 2px left accent bar in `primary`) regardless of whether
the content area is light or dark. This is a deliberate, specific choice, not an oversight:

- It's the recognizable premium-enterprise pattern (Vercel, Linear, Notion, GitHub's newer shell,
  Datadog) — a persistent anchor that doesn't change with content theme, so the product always
  has one consistent brand identity.
- It gives Cryo's own visual identity (the cool "glacier" ice-blue palette already used in
  `docs/ARCHITECTURE_AND_AI_STRATEGY.md`'s diagram) a permanent home in the product, whichever
  theme the operator's content area is in.
- It gives strong, constant wayfinding — the eye always knows where "current page" lives, even
  while everything else on screen re-themes.

### 1.4 Semantic color — grades, priority, status

These already exist and are already well-chosen; refine only for AA contrast and dark-mode
parity, don't change the meaning.

| Signal | Light | Dark | Meaning |
|---|---|---|---|
| Grade A | `#1E8E5A` | `#3FBE85` | Excellent health |
| Grade B | `#3D6FD4` (= primary) | `#5B9CE8` (= primary) | Good |
| Grade C | `#A9791E` | `#E0B03D` | Fair, needs attention soon |
| Grade D | `#C1631B` | `#F0904F` | Poor |
| Grade F | `#B23A2E` | `#F0665A` | Critical |
| Cost — current | `#3D6FD4` | `#5B9CE8` | "Now" bars/lines |
| Cost — projected | `#0F9B8E` | `#35C7B8` | "After maintenance" |
| Cost — savings | `#1E8E5A` | `#3FBE85` | Reclaimed value (= Grade A family, both mean "good outcome") |

**One refinement to make:** `PriorityTag`'s `LOW` value currently reuses AntD's `blue`, which
visually collides with "Grade B" and with the primary brand color even though priority is an
*urgency* signal, not a *quality* signal. Change `LOW` to a neutral slate tag (`inkFaint` on
`surfaceSunken`) so the eye never confuses "low priority" with "good grade" or "primary action."
`HIGH` stays in the red/critical family, `MEDIUM` stays amber — both correctly borrow from the
grade ramp since urgency and severity are related concepts.

### 1.5 Typography

**Recommendation: IBM Plex Sans (UI/headings/body) + IBM Plex Mono (data-dense/technical
content).** Reasoning: Plex was designed by IBM specifically for enterprise/technical products —
it has real character (a touch of geometric warmth, confident numerals) without reaching for the
generic Inter/Space Grotesk default every AI-generated dashboard reaches for, it's fully open
(SIL OFL, self-hostable, no licensing risk), and Plex Sans and Plex Mono are drawn to share
proportions, so headings and the mono numbers/IDs sitting next to them actually look designed
together rather than mismatched. The app's `ConfigProvider` already sets a `fontFamily` token
today (currently `Inter, ui-sans-serif, …`) — this is a one-line change, not a rebuild.

| Role | Face | Where |
|---|---|---|
| UI / body | IBM Plex Sans | All prose, labels, table cells, buttons, form fields |
| Data / technical | IBM Plex Mono | Table/run/job IDs, cron expressions, JSON editors (preset params/guardrails, source/destination config), rendered SQL in Plan Preview, byte/count columns where digit alignment matters |
| Fallback stack (sans) | `-apple-system, "Segoe UI", Roboto, sans-serif` | If self-hosted font isn't loaded yet |
| Fallback stack (mono) | `ui-monospace, Menlo, Consolas, monospace` | Same |

**Type scale** (kept dense on purpose — this is a scan-and-operate tool, not an editorial page):

| Token | Size / line-height | Weight | Use |
|---|---|---|---|
| `caption` | 11 / 16 | 500 | Chart axis ticks, tag text, eyebrow labels |
| `small` | 12 / 18 | 400 | Helper text, secondary table cell content |
| `body` | 13 / 20 | 400 | Default — table cells, form labels, body copy |
| `bodyStrong` | 14 / 20 | 600 | Section labels, table primary column (table name) |
| `subtitle` | 15 / 22 | 600 | Card titles |
| `pageTitle` | 20 / 28 | 700 | `PageHeader` title |
| `heroStat` | 28 / 32 | 700, tabular-nums | `StatStrip` big numbers |

**Global rule: `font-variant-numeric: tabular-nums` everywhere digits appear in a column** — every
table numeric column, every `StatStrip` value, every cost figure. This is not currently set and is
one of the highest-leverage, lowest-effort changes for making the product feel precision-built
rather than default-rendered — misaligned digits in a financial/ops table read as unpolished
immediately to this specific audience.

### 1.6 Spacing, radius, elevation

- **Spacing scale:** 4 / 8 / 12 / 16 / 20 / 24 / 32 / 40px. Matches the `ConfigProvider` tokens
  already set (`margin: 12, padding: 12`) — extend, don't replace.
- **Radius:** keep the existing 8px (`borderRadius: 8`) — it's already consistent across the app
  and changing it would be churn without a real payoff.
- **Elevation:** hairline borders (`border` token) as the primary separator everywhere. Reserve
  actual shadow for things that float above content: `Drawer`, `Modal`, `Popover`/`Select`
  dropdowns, and the sticky `BulkAssignBar`. Shadow spec: `0 8px 24px rgba(9,14,20,0.12)` in light,
  `0 8px 24px rgba(0,0,0,0.45)` in dark — soft and low-opacity, never a hard drop shadow. Cards and
  table rows get **no shadow**, only the hairline border — this is what separates "precision tool"
  from "generic dashboard template."

### 1.7 Motion

- Hover/press/focus transitions: 120ms ease-out.
- Drawer/Modal open: 200ms ease-out slide + fade (AntD default is close, keep it).
- New live-console log line (`RunConsole`): fade + 4px slide-up, 150ms, so streaming output feels
  alive without being distracting.
- Chart transitions (Recharts): 250–300ms ease on data change.
- `Sparkline` draws in once on first mount (~400ms), not on every re-render.
- Respect `prefers-reduced-motion`: disable slide/draw-in animations, keep state changes instant.

### 1.8 Iconography

Keep `@ant-design/icons`, **outlined style only** — never mix outlined and filled variants in the
same view (the current nav is already consistently outlined; keep it that way as new icons are
added). 16px in nav and standalone buttons, 14px inline within table cells and tags.

---

## 2. Global shell and navigation

Six top-level destinations already exist and are the right set — don't add a seventh without a
real reason: **Health, Cost, Tables, Presets, Runs, Connections.** Order them by operator
frequency, which the current order already mostly reflects (Health and Cost first, as the
"what needs attention" surfaces; Connections last, as the least-frequently-touched setup page).

- **Sidebar (212px, always dark per §1.3):** brand mark + wordmark at top, then the six nav items
  with icon + label. Active item: white text, left accent bar in `primary`, subtle `primarySoft`-
  at-10%-opacity background wash (not a hard block color — softer reads as more premium).
- **Topbar:** currently holds a static subtitle and a dev-role `Select` (viewer/operator/admin) —
  correct for the current auth stage. When real auth lands (per `docs/EXECUTION_PLAN.md` Phase 1),
  this becomes: environment/catalog context on the left (which datalake/cluster you're looking at, if
  multi-tenant), user identity + theme toggle + role badge on the right.
- **Theme toggle:** a simple sun/moon icon button in the topbar, persisted to `localStorage`,
  defaulting to the OS preference (`prefers-color-scheme`) on first load.

---

## 3. Cross-cutting patterns

These components are shared across pages — get them right once and every page benefits.

### 3.1 The status/badge system (`components/status.tsx`)

Four distinct badge types exist and must stay visually distinct from each other so an operator
never has to think about which system a color belongs to:

| Component | Signal type | Visual form |
|---|---|---|
| `GradePill` | Health quality (A–F) | Filled tag, grade color background at low opacity, grade color text — the single most important glanceable signal in the product, it should be the most saturated color on any row it appears in |
| `PriorityTag` | Urgency (HIGH/MED/LOW) | Outlined tag, not filled — visually quieter than `GradePill` so the two never compete for attention in the same row |
| `StatusTag` | Job/run lifecycle state | Filled tag; `running` gets a subtle pulsing dot prefix (via a small animated `Badge.status="processing"` dot, which AntD already supports) so in-progress rows are scannable without reading text |
| `HealthBadge` | Boolean enabled/healthy | AntD `Badge` dot + text, smallest visual weight of the four — used for binary on/off states like source/destination enablement |

### 3.2 Tables (the core surface of this product)

Every primary page centers on a dense `Table`. Conventions to apply everywhere:

- `size="small"`, sticky header, horizontal scroll (`scroll={{ x: … }}`) rather than column
  truncation — this audience would rather scroll than lose a column.
- The identity column (table name, run ID, preset name) is always **fixed left**, always a link,
  always `bodyStrong` weight — everything else in the row is `body` weight so the eye anchors on
  identity first.
- Every numeric column is right-aligned with `tabular-nums` (§1.5).
- Sort indicators only on columns that are actually meaningful to sort by (already the case —
  `grade`, `compaction_priority`, size/count/cost columns are sortable; identity and config columns
  are not).
- Row hover: `surfaceSunken` background, no border change, no shadow — quiet, not a card-lift
  effect.
- Empty state (`EmptyState`): always a specific title + a specific next action, never a generic
  "No data." Every page already does this correctly (e.g. "No tables match this view — Run a scan
  or clear filters to browse the datalake") — keep this discipline for every future table.

### 3.3 Filtering and bulk selection (`FilterBar`, `BulkAssignBar`)

This is the product's signature interaction (filter the datalake → select all matching → assign/run)
and should feel the most polished:

- `FilterBar` sits directly under `PageHeader`, filters render as removable chips once applied so
  the current view's criteria are always visible without opening a panel.
- Selection has two modes already modeled correctly: page-selection (`rowSelection` checkboxes) and
  "select all N matching this filter" (`allMatching`) — the bulk bar must always show which mode is
  active and the exact count, e.g. "1,204 tables selected (all matching filter)" vs. "12 tables
  selected," never just "N selected."
- `BulkAssignBar` is a **sticky bottom bar**, the one place in the app that earns real elevation
  (shadow per §1.6) since it floats over table content — Assign / Preview / Run actions plus a
  Clear, always visible once a selection exists, never requiring a scroll back up.
- `PlanPreview` (dry-run modal): shows rendered SQL/plan per table before execution — this is a
  trust-building surface, give the rendered SQL its own `surfaceSunken` code block in Plex Mono
  with syntax-appropriate spacing, not an unstyled `<pre>`.

### 3.4 Forms and drawers (Presets, Connections)

- All create/edit forms live in a right-side `Drawer`, not a page navigation — correct already,
  keep it, since it preserves the list context behind it.
- JSON-editing fields (preset `params`/`guardrails`, source/destination `config`) use
  `Input.TextArea` today with a `mono-input` class — formalize this as a real lightweight code
  editor affordance: monospace font (Plex Mono), line-numbers optional, and inline JSON-parse-error
  messaging at the field level instead of only a toast on submit failure (currently
  `message.error("Params and guardrails must be valid JSON.")` only fires on submit — move that
  validation to the field's `onBlur` too, so the error surfaces before the user commits).
- Secrets: the `secret_ref` field's placeholder copy ("Encrypted secret reference, never raw
  credentials") is exactly right — keep that discipline anywhere a credential-adjacent field
  appears.

### 3.5 Notifications

AntD `message` toasts are used consistently for mutation results today — keep that pattern; add
one convention: **success toasts that reference an ID (e.g. "Run queued: `{run_id}`") should make
the ID a clickable link to that resource**, not plain text, so the confirmation doubles as
navigation.

### 3.6 Loading and error states

- Loading: `Skeleton active` for full-page loads (already used in `DatalakeHealth`/`CostSavings`),
  `Table`'s own `loading` prop for in-place table refreshes — keep this split, don't skeleton an
  already-rendered table on refetch, it causes layout jank.
- Degraded-backend state: both dashboard pages already have a graceful fallback (`Alert type="info"`
  + client-side derivation from `/tables` when the dashboard endpoint errors) — this is exactly the
  right pattern (never show a blank dashboard when a derivable fallback exists) and should be the
  template for any future dashboard endpoint.

---

## 4. Page-by-page specification

### 4.1 Datalake Health (`/health`) — the landing page

**Job:** answer "is the datalake OK, and if not, where do I look first" in under five seconds.

| Region | Contents |
|---|---|
| Header | Title, subtitle, "View cost savings" action |
| `StatStrip` | Tables · Datalake size · Small files · Needs compaction · S3 monthly now · After maintenance · Monthly savings · Reclaimable — 8 hero numbers, `heroStat` scale, tabular-nums |
| Grade distribution | Donut chart, slice colors = grade ramp (§1.4) — the fastest "is the datalake healthy" glance in the product |
| Cost by schema | Grouped bar (Now vs. After maintenance), top 12 schemas by cost/savings |
| Top offenders | Table: name, grade, priority, small files, size — the datalake's worst tables, sorted, linking to Table Detail |
| Drift watch | Small list of recent scan deltas — currently minimal (timestamp + small-file count); leave as-is until Phase 2/3 features (streaming/commit-velocity detection, metadata-ratio) land, then this panel is the natural home for those new signals |

**Visual note:** the grade donut is the one place a chart legitimately carries the whole semantic
palette at once — give it slightly more visual weight (larger `outerRadius`, a centered total-count
label) than the other charts on this page, since it's the page's actual thesis statement.

### 4.2 Cost & Savings (`/cost`)

**Job:** turn storage spend into an actionable, defensible number — this is the page most likely
to be screenshotted for a leadership update, so it needs to look immediately credible.

| Region | Contents |
|---|---|
| `StatStrip` | S3 monthly now · After maintenance · Monthly savings · Reclaimable storage · Precision coverage % |
| Current → projected waterfall | `CostSavingsWaterfall` — current cost breaking down to projected, by operation |
| Savings by operation | `SavingsByOperation` — per-operation dollar contribution, colored per `operationColors` (compaction/expiry/orphan/manifest already have distinct hues) |
| Cost by schema | Grouped bar, same convention as Health page |
| Cost trend | Line chart, current cost vs. available savings over time |
| Top opportunities | Table with row-selection, Preview/Run per-row actions, feeds the same `BulkAssignBar` |

**Visual note:** `CostPrecisionBadge` should read as a savings-basis explanation, not a caveat. Use
plain labels such as `Quick estimate`, `Iceberg metadata`, and `Storage checked`, and frame it
visually as informational (neutral blue/gray), never as a warning color, since lower precision just
means "run a fuller scan," not "something is wrong."

### 4.3 Table Explorer (`/tables`)

**Job:** find the tables that match a specific operational question and act on all of them at once.

Columns present today, in fixed order: Table (link, fixed-left) · Grade · Priority · Needs
(compaction) · Small files (+ `Sparkline`) · Total files · Size · Savings/mo · Reclaimable ·
Precision · Partition · Seen. A "Savings view" toggle re-sorts by `projected_savings_usd_month`
descending — this is effectively a saved-sort shortcut; as a cheap follow-on, consider 2–3 more of
these (e.g. "Attention view" sorted by grade ascending/priority) rather than building a full
saved-views feature yet (that's `RECOMMENDATIONS.md`'s explicit backlog item, don't build it early).

**Visual note:** the inline `Sparkline` in the Small Files column is a nice, unusual touch already
in the codebase — lean into it rather than removing it for simplicity; it's exactly the kind of
small, real signal (not decoration) that makes a dense table feel premium instead of just busy.

### 4.4 Table Detail (`/tables/:tableId`)

**Job:** everything about one table, for someone about to decide whether to run maintenance on it
right now.

| Region | Contents |
|---|---|
| Header | Table name, Assign-preset select, Preview, Run |
| Health summary | `GradePill` + `PriorityTag` + `HealthBadge`, `StatStrip` (score/size/files/small files/partitions), `Descriptions` (partition col/type, snapshots, manifests, delete files, effective preset + provenance) |
| Cost & savings | `CostPrecisionBadge` + last-scan time, `StatStrip`, live/dead/metadata/orphan byte `Descriptions`, `SavingsByOperation` |
| History | Small sparkline-style line chart, small-file count + overall score over time |
| Partitions | Table: value, files, small files, size, compaction status |
| Audit timeline | `Timeline`, colored green/red by success/failure, action + engine + duration per entry |

**Visual note:** this page has the highest information density in the product (six distinct data
regions). Keep the 12-column grid discipline already in place (`xl={16}/{8}/{8}/{15}/{9}`) — don't
let any single card grow tall enough to force excessive scrolling before the Audit timeline is
visible; that timeline is often the first thing an operator wants after a run, since it's the
proof something happened.

### 4.5 Presets (`/presets`)

**Job:** define *what* maintenance means for a group of tables, safely.

- List: name + description, mode, destination, priority, enabled (`StatusTag`), actions
  (Edit / Rules / Simulate).
- **Edit drawer:** name, description, destination, mode (light/standard/deep), priority, enabled,
  operations (checkbox group — already correctly ordered expire→orphan→compact→manifest per the
  planner's enforced sequencing), schedule cron, params JSON, guardrails JSON.
- **Rules drawer:** reuses `FilterBar` to build a predicate-based matching rule, lists existing
  rules with their raw predicate JSON.
- **Simulator drawer:** runs the preset's current rule set against the live datalake without executing
  anything — shows matched count, total size, small files, and a sample table. This is the
  single best trust-building feature in the product (per `ARCHITECTURE_AND_AI_STRATEGY.md`'s
  transparency principle) — visually, the simulation result should look distinctly "read-only/
  dry-run" (e.g. a dashed-border result panel, matching the same dashed-border convention used for
  the new Events module in the architecture diagram) so it's unmistakable that nothing has been
  executed yet.

### 4.6 Runs (`/runs`, `/runs/:runId`)

**Job:** know what's running right now, and watch it live.

- Recent runs table: short ID (linked), status, kind, trigger, job count, created time, Cancel
  (disabled once terminal).
- Selecting a run opens `RunConsole` alongside the list (list narrows from 24 to 11 columns, console
  takes 13) rather than navigating away — keep this split-view pattern, it's the correct choice for
  "keep watching the list while focused on one run."
- `RunConsole`: live job state via polling (`refetchInterval: 5000` on the list; the console itself
  should stream faster once the WebSocket endpoint noted in `current-state.md` lands). New log
  lines get the fade+slide-in treatment from §1.7.

### 4.7 Connections (`/connections`)

**Job:** the least-frequently-visited but highest-blast-radius page — misconfiguring a destination
can point maintenance at the wrong cluster.

- Two tabs, Sources and Destinations — correct separation, reinforces the "sources ≠ destinations"
  hard requirement from `agents.md`.
- Sources: name, type (rest/hive/glue/polaris/nessie), enabled, config keys (not values — good,
  don't ever render raw config values that might contain endpoints/credentials in a table cell),
  Edit/Enable-Disable.
- Destinations: name, engine (trino/spark_emr), enabled (`HealthBadge`), off-peak window, Edit/
  Test/Enable-Disable. "Test" hits the real capacity-probe endpoint and renders the raw JSON result
  in an `Alert` — keep this raw-JSON transparency for an ops audience; don't prettify it into a
  summary that could hide a real error detail.

---

## 5. Component library reference

| Component | Current role | Elevation guidance |
|---|---|---|
| `PageHeader` | Title + subtitle + actions, used on every page | No visual change needed; ensure title always uses `pageTitle` scale, subtitle always `inkSoft` |
| `FilterBar` | Predicate-builder chips | Applied filters render as removable chips in `primarySoft` background |
| `BulkAssignBar` | Sticky bottom action bar | Only component besides Drawer/Modal that gets a shadow (§1.6) |
| `PlanPreview` | Dry-run modal, rendered SQL | SQL block in Plex Mono on `surfaceSunken` |
| `RunConsole` | Live job/log view | New-line fade+slide, respects reduced-motion |
| `CostPrecisionBadge` | Estimate vs. measured cost | Always neutral-toned, never a warning color |
| `CostSavingsWaterfall` | Current→projected breakdown | Semantic cost colors (§1.4) |
| `SavingsByOperation` | Per-op $ breakdown | `operationColors` — already has 4 distinct, well-chosen hues; keep |
| `GradePill` / `PriorityTag` / `StatusTag` / `HealthBadge` | Status system | See §3.1 — keep visually distinct from each other |
| `StatStrip` | Hero numbers | `heroStat` scale, tabular-nums, no more than ~8 items before it should split into two rows |
| `ChartFrame` | Recharts wrapper + empty state | Consistent axis-tick styling (`chartAxisTick`) and tooltip styling (`chartTooltipProps`) already centralized — good, keep all new charts routed through these |
| `EmptyState` | Empty-table/chart messaging | Always specific title + specific next action, never generic |
| `Sparkline` | Inline SVG trend | `currentColor` stroke so it inherits the cell's text color automatically |

---

## 6. Accessibility checklist

- All semantic color pairs (grade/status text-on-background) must hit **WCAG AA (4.5:1)** in both
  themes — the hex values in §1.4 were chosen with this in mind; verify with a contrast checker
  before shipping the dark theme, since dark-mode semantic colors are the ones most likely to drift
  out of range if adjusted later.
- Every interactive element gets a visible `focusRing` (§1.1/1.2) — never rely on browser default
  outline removal without a replacement.
- `Sparkline` and chart SVGs already carry `role="img"` + `aria-label` in one place (`Sparkline`) —
  extend the same discipline to every Recharts chart via a wrapping `role="img"` + a generated
  `aria-label` summarizing the chart's data (e.g. "Grade distribution: 40 A, 12 B, 3 C, 1 D, 0 F").
- Status conveyed by color always has a redundant non-color signal too — text label (already true
  for all four badge components) or icon; never color alone.
- Respect `prefers-reduced-motion` globally (§1.7).
- Keyboard: table row selection, drawer forms, and the bulk-assign bar must all be fully operable
  without a mouse — verify tab order in each drawer explicitly since AntD's default order can skip
  unexpectedly when custom fields (like the JSON textareas) are involved.

---

## 7. What "premium enterprise" means here, concretely

To keep this calibrated rather than vague:

- **Do:** tabular-nums everywhere, a considered two-typeface system, hairline borders instead of
  shadows, a persistent dark sidebar as brand anchor, specific empty states, semantic color spent
  only on grade/status/cost meaning, restrained motion.
- **Don't:** hero sections, decorative gradients, more than one saturated non-semantic accent,
  centered data-dense layouts, generic stock-icon illustrations, animated page transitions,
  skeuomorphic card shadows on every panel.
- **The tell that separates the two:** everything in this document either encodes real information
  (a grade, a status, a trend, a precision level) or improves scanability (spacing rhythm, digit
  alignment, fixed identity columns). Nothing here is decoration for its own sake — that's the
  actual definition of "looks like Linear/Datadog/Snowsight built it" rather than "looks
  AI-generated."

---

## 8. Implementation notes

- Token changes land in `web/src/lib/theme.ts` (extend `cryoColors` with the dark-theme
  equivalents from §1.2/1.4, likely as a `cryoColorsDark` export selected by a theme context) and
  `web/src/App.tsx`'s `ConfigProvider` (switch `theme.algorithm` between `theme.defaultAlgorithm`
  and `theme.darkAlgorithm` based on the same theme context, and update `fontFamily` to lead with
  `"IBM Plex Sans"`).
- Self-host IBM Plex Sans/Mono (woff2, `@font-face`) rather than a font CDN — consistent with
  keeping the app fully self-hosted, and avoids a third-party request from an internal ops tool.
- None of this requires new dependencies — AntD 6's `theme.darkAlgorithm`, Recharts, and the
  existing component set already support everything specified here.

---

## 9. Implementation status ✅

**Implemented 2026-07-03:**

| Feature | Status | Notes |
|---------|--------|-------|
| Dark theme | ✅ | `cryoColorsDark` palette, CSS variables, `data-theme` attribute |
| Theme toggle | ✅ | Sun/moon icon button in topbar, persisted to localStorage |
| Always-dark sidebar | ✅ | Fixed dark surface regardless of content theme |
| IBM Plex fonts | ✅ | Sans + Mono via Google Fonts CDN (can be self-hosted later) |
| `tabular-nums` | ✅ | Already in `body` CSS |
| Focus ring | ✅ | `:focus-visible` styling with `--focus-ring` variable |
| PriorityTag LOW fix | ✅ | Changed from blue to neutral slate (`default`) |
| Log line animation | ✅ | Fade + 4px slide-up via CSS keyframes |
| Reduced motion | ✅ | `prefers-reduced-motion` media query |

**Files changed:**
- `web/src/lib/theme.ts` — `cryoColorsLight`, `cryoColorsDark`, `sidebarColors`, `CryoColors` type
- `web/src/lib/themeContext.tsx` — new theme context with `useTheme` hook
- `web/src/App.tsx` — `ThemeProvider`, `ThemeToggle`, dynamic `ConfigProvider`
- `web/src/styles.css` — CSS variables, dark theme rules, sidebar styling, animations
- `web/src/components/status.tsx` — PriorityTag LOW color fix
