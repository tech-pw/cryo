# Design

## Visual Strategy

Cryo is a dense, restrained operations dashboard. The shell is light: a frost page, white cards, and a light sidebar. Plan owner decision, 2026-09-28: retire the dark navigation rail and replace the cobalt accent with Apache Iceberg blue. Status colours stay independent of that blue so health and failure never read as decoration.

The UI should feel calm and precise: operators scan health and maintenance posture without decorative noise. Colour is for action, selection, risk, and system state only. PRODUCT.md bans still apply: no gradients, glass, hero metrics, decorative motion, or illustrations.

## Color Tokens

Canonical tokens live in `web/src/styles/tokens.css`. `web/src/lib/theme.ts` reads those CSS variables, with the same hex as fallback, so charts follow light and dark. Keep base UI near-neutral. Iceberg blue is for primary actions, selected nav, links, and charts. Brand blue never means a grade or cost; grade B is olive. Do not repaint `--primitive-neutral-150`; the page colour is `--primitive-page`, aliased by `--color-layout-bg`. Cards use `--primitive-surface`.

```css
:root {
  --primitive-page: #f5fafd;
  --primitive-surface: #ffffff;
  --primitive-neutral-150: #f3f5f8;
  --color-layout-bg: var(--primitive-page);
  --color-panel: var(--primitive-neutral-150);
  --primitive-accent-500: #277abe;
  --primitive-accent-600: #266fa8;
  --primitive-accent-100: #eaf7fd;
  --color-link: #266fa8;
  --color-on-tint: #1f5f94;
  --color-control-border: #808b9e;
  --focus-ring-color: #277abe;
  --sidebar-bg: #f5fafd;
  --sidebar-active-bg: #eaf7fd;
  --sidebar-text: #5c667a;
  --sidebar-text-active: #1f5f94;

  --grade-a: #1e8e5a;
  --grade-b: #4d7c0f;
  --grade-c: #a9791e;
  --grade-d: #c1631b;
  --grade-f: #b23a2e;
}
```

Dark mode uses the same roles: page `#0b0f16`, sidebar `#0e141f`, solid `#68c1ea`, link and active text `#90d4f0` on `#0f2a40`. Success, warning, danger, and grades A, C, D, and F are unchanged. Legacy `--primary` is the link colour so older CSS text meets 4.5:1; `--color-primary` stays the solid `#277abe`.

Contrast rules (WCAG 2.2, checked by `web/src/styles/tokens.contrast.test.ts`):
- Text pairs are at least 4.5:1. Active and selected text on frost tints uses `#1f5f94`, not `#277abe` (4.17:1 on `#eaf7fd`, fails). Links and primary text on the frost page use `#266fa8` (5.09:1), not `#277abe` (4.33:1, fails).
- White on `#277abe` is 4.55:1 for button labels. Dark button text is near-black on `#68c1ea`.
- Sidebar text `#5c667a` on `#f5fafd` is 5.49:1. Muted `#5c667a` on white is 5.77:1.
- `--color-ink` on page, card, and panel surfaces stays above 7:1.
- `--color-muted` is for secondary labels and must stay readable at 4.5:1 on main surfaces; use `--color-muted-subtle` only for icons, dividers, metadata, or disabled text with another affordance.
- Form control borders (`input`, `select`, `checkbox`) use `#808b9e` so the boundary is at least 3:1 on white, the frost page, and selected frost. Card and table borders stay decorative.
- Focus rings use `#277abe`, never the light ice blues.
- Never communicate grade, error, warning, success, or running state through colour alone.

## Typography

Use IBM Plex Sans for UI copy and IBM Plex Mono for SQL/JSON/IDs. Both are bundled locally (no Google Fonts dependency): `"IBM Plex Sans", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`.

Use a fixed product scale:
- Page title: 24px / 32px, weight 650.
- Section title: 18px / 26px, weight 650.
- Body: 14px / 22px, weight 400.
- Dense table/body: 13px / 20px, weight 400.
- Labels and column headers: 12px / 16px, weight 550.
- Microcopy: 11px / 16px, weight 500.

Enable `font-variant-numeric: tabular-nums` for metrics, durations, sizes, counts, percentages, costs, grades, and run IDs. Use a monospace stack for table refs, SQL, JSON predicates, job IDs, and engine query IDs: `ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`.

## Layout

Use a compact app shell: a fixed light sidebar for primary sections, a white topbar for environment, search, user, and global actions, and a scrollable frost content region. Keep navigation familiar and stable. The sidebar is not a dark rail (plan owner decision, 2026-09-28).

Recommended frame:
- Sidebar width: 224px desktop, collapsible to icons at tablet, drawer on mobile. Frost background (`--sidebar-bg`), 1px right border, active item as a frost pill with a 2px Iceberg-blue leading rail, deep-blue text, and an accent icon. Inactive icons sit quieter than their labels. Section labels are muted sentence case, never uppercase. Home and Settings are pinned; Operate (Tables, Runs, Decisions) and Understand (Cost, Insights) group the rest. Press scales a link to 0.97; hover changes the fill only, never the committed color. A scroll-edge fade replaces the brand divider, and only while nav content passes under the brand.
- Topbar height: 48px, white, 1px bottom border. In dark mode the topbar follows `--color-bg`, not a forced white.
- Page background: frost (`--color-layout-bg` / `--primitive-page`), not `--primitive-neutral-150`.
- Page padding: 20-24px desktop, 16px tablet, 12px mobile.
- Content max width: none for tables and consoles; 960-1120px for forms and settings.
- Cards sit on the frost page: white, 12px radius, 1px border, faint shadow. Panels and sunken toolbars stay on `--primitive-neutral-150`.
- Operator tables keep compact density. Do not force comfortable rows onto runs, jobs, actions, or catalog.

Responsive rules:
- Table-first screens remain table-first on desktop and tablet; horizontal overflow is acceptable when paired with sticky key columns.
- Under 768px, collapse secondary columns, move filters into a drawer, and keep primary actions reachable from a sticky bottom bar.
- Tabs, segmented controls, and table/view toolbars must contain horizontal scrolling or wrap inside
  the component; they must never create page-level overflow at a 320 CSS px viewport.
- Do not use fluid typography; change structure at breakpoints instead.

## Data Tables

Tables are the primary product surface. Use the shared TanStack `DataTable` in `web/src/components/ui/data-table.tsx`. It is built on Radix primitives and styled with the CSS variables in `web/src/styles/tokens.css`. Pass pagination and row selection into `DataTable`; keep sorting and filtering in the page toolbar. Do not add a one-off table.

Density:
- Default row height: `--table-row-height-default` (48px) on table and detail pages. Operator tables (runs, jobs, actions, catalog) use compact density (36px).
- Compact row height: `--table-row-height-compact` (36px) for operator-heavy screens.
- Sticky header, sticky first column for table name, and sticky bulk action bar.
- Align numbers right, labels left, statuses center or left depending on scanability.
- Use tabular numerics, compact units, and clear empty cells (`Not scanned`, `None`, `Unknown`) instead of visual blanks.

Filtering:
- Put common filters in a compact toolbar: source, schema, health grade, size, small-file %, preset, partitioning, drift, last scan.
- Advanced predicates live in a drawer or popover with readable chips.
- Bulk selection must reflect the backend model: page selection is distinct from "select all matching filter".

## Components

### Page Headers

Use one open page-header region with breadcrumbs, a single 24px `h1`, a concise 68–72 character
description, and right-aligned primary actions. Separate the header from working content with
spacing and a quiet bottom divider; do not turn it into a hero card. On narrow screens, actions wrap
below the title and remain left-aligned.

### Cards And Metrics

Cards may use the shared `--shadow-card` token for slight separation from the workspace, but they
must remain neutral and must not lift on hover unless the entire card is interactive. KPI tiles use
one label, one dominant tabular value, and optional context/delta. Emphasis uses a stronger border
and a pale primary surface rather than a decorative side stripe.

### Tokens And Primitives

Components use the CSS variables in `web/src/styles/tokens.css` and the primitives in `web/src/components/ui/`. Do not style each screen ad hoc, and do not add Ant Design.

Keep component vocabulary consistent across screens. Tables use the shared TanStack `DataTable`. Forms, dialogs, drawers, tabs, menus, selects, tooltips, and notifications use those Radix primitives.

### Buttons

Primary buttons are for one committed action per region: assign preset, run now, save, connect. Secondary buttons are default. Danger actions use error color and require clear confirmation when destructive or expensive.

Button copy should be verb-first and specific: `Assign preset`, `Run maintenance`, `Preview plan`, `Test connection`, `Cancel run`.

### Forms

Use compact vertical forms for create/edit flows. Group by operational model: identity, connection, schedule, guardrails, rules. Put help text close to risky fields, not in a separate doc-like panel.

Connection secrets should never render back after save. Use health-check results inline with timestamp, status, and next step.

Every invalid field uses a stable label/error relationship and `aria-invalid`. On failed submit,
focus a summary whose links move to each invalid field without clearing entered values. Common tasks
use structured controls; raw JSON remains an explicitly advanced mode.

### Filters

Filters should feel like a query builder without exposing implementation detail. Use chips for active filters, quick reset per chip, and one clear `Clear all` action. Avoid redundant labels when the value already carries context.

### Tags And Pills

Use pills for status, engine, mode, grade, preset, source, destination, and drift. Filled pills are reserved for meaningful state; neutral outline pills are for metadata. Grade pills must include the letter and a text label in tooltips or detail views.

### Modals And Drawers

Prefer drawers for inspection, preview, logs, and multi-step configuration because they preserve table context. Use modals for short confirmations and irreversible actions only. Plan preview should use a modal or drawer with clear sections for estimated load, affected partitions, rendered SQL, and guardrail warnings.

### Empty, Loading, And Error States

Use skeletons for loading table rows, panels, and charts. Empty states should teach the next action: connect a source, run a scan, create a preset, adjust filters. Errors should state what failed, likely cause if known, and the safest next action.

### Charts

Use Recharts with restrained colors and direct labels where possible. Charts should answer operational questions: grade distribution, cost by schema, top offenders, run throughput, failure rate, engine load. Avoid decorative donut overload; pair charts with the exact rows behind them when useful.

Every chart must provide the same values in a nearby labelled table/list. Chart-driven filters must
also exist as keyboard-operable semantic controls; redundant visual charts may be hidden and made
inert to assistive technology. Never depend on hover, pointer input, or color alone.

### Persistent Context

Meaningful tabs, filters, pagination, inspected records, and time ranges belong in the URL. Preserve
unrelated query parameters when changing one control so refresh, back/forward, bookmarks, and
incident handoffs reproduce the same view.

### High-Impact Actions

Review boundaries state the exact scope, engine, consequence, and recovery limits. Name dry runs as
dry runs and send an explicit non-committing API request; do not imply a repair or commit when the
backend only inspects. Routine reversible actions should not gain unnecessary confirmation.

## Motion

On 2026-09-28 the owner asked for purposeful motion. That narrows the page-load ban to one first-paint entrance (at most four sections, 320 ms total). Motion shows that something changed. It is not decoration.

Animate only `transform` and `opacity`. Heights expand with `grid-template-rows`. Ease-out for entrances, ease-in for exits. No bounce. Numbers never tween. A chart draws once on mount and does not replay on poll. Skeleton shimmer stops after 5 seconds, then reads "Still loading…".

Tokens: `--motion-fast` 120 ms, `--motion-base` 180 ms, `--motion-medium` 220 ms, `--motion-slow` 280 ms, `--motion-layout` 360 ms, `--motion-highlight` 800 ms.

Respect reduced motion: spatial movement is removed; opacity fades and necessary progress stay.

```css
@media (prefers-reduced-motion: reduce) {
  * {
    animation-duration: 1ms;
    transition-duration: 1ms;
    scroll-behavior: auto;
  }
}
```

## UX Copy

Write concise operator language. Prefer concrete nouns and verbs over marketing phrasing.

Use:
- `Table health`
- `Assign preset`
- `Select all matching tables`
- `Preview SQL and load`
- `Deferred by cooldown`
- `Skipped: engine at capacity`

Avoid:
- redundant labels like `Table name: demo.orders` when the column already says `Table`;
- vague success text like `All set`;
- playful empty states;
- alarmist warnings that do not explain the actual risk.

## Explicit Bans

No gradient text, glassmorphism as a default, hero-metric templates, decorative side-stripe borders, playful hero cards, identical marketing card grids, tiny uppercase eyebrows on every section, or numbered section markers used as decoration.

No decorative motion, display fonts in labels/buttons/data, custom form controls for flavor, non-standard modals, heavy color on inactive states, or modal-first flows when inline, drawer, or progressive disclosure would preserve context.
