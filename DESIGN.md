# Design

## Visual Strategy

Cryo is a dense, restrained operations dashboard with a dark, always-on navigation rail and neutral content surfaces. The UI should feel calm and precise: operators should scan health and maintenance posture quickly without decorative noise.

Use cool neutral surfaces, one cobalt primary, and explicit status colors for health/state transitions. Color is for action, selection, risk, and system state only.

## Color Tokens

Use the shipped Hex token system as canonical (see `web/src/styles.css` and `web/src/lib/theme.ts`). Keep base UI near-neutral and reserve saturated color for primary actions, selected rows, active nav, alerts, charts, and health grades.

```css
:root {
  --bg: #ffffff;
  --layout-bg: #f3f5f8;
  --panel: #f3f5f8;
  --line: #e1e6ed;
  --ink: #131b24;
  --muted: #5c667a;
  --primary: #3566c8;
  --primary-hover: #2f5bb7;
  --primary-soft: #e8effc;
  --sidebar-bg: #0c121b;
  --sidebar-text: #c7d2de;
  --sidebar-text-active: #ffffff;

  --grade-a: #2e9f6b;
  --grade-b: #2f8fbd;
  --grade-c: #c08c2d;
  --grade-d: #c9791e;
  --grade-e: #c2562b;
  --grade-f: #b23a2e;
}
```

Contrast rules:
- `--color-ink` on `--color-bg`, `--color-surface`, and `--color-panel` must exceed 7:1.
- `--color-muted` is for secondary labels and must stay readable at 4.5:1 on main surfaces; use `--color-muted-subtle` only for icons, dividers, metadata, or disabled text with another affordance.
- Text on saturated fills must meet AA contrast. The light theme uses white on its darker cobalt;
  the dark theme uses near-black on its lighter cobalt and status fills.
- Never communicate grade, error, warning, success, or running state through color alone.

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

Use a compact app shell: fixed left sidebar for primary sections, topbar for environment, search, user, and global actions, and a scrollable content region. Keep navigation familiar and stable.

Recommended frame:
- Sidebar width: 224px desktop, collapsible to icons at tablet, drawer on mobile.
- Topbar height: 48px.
- Page padding: 20-24px desktop, 16px tablet, 12px mobile.
- Content max width: none for tables and consoles; 960-1120px for forms and settings.
- Use one surface layer for the main workspace and a subtle panel layer for filters/toolbars.

Responsive rules:
- Table-first screens remain table-first on desktop and tablet; horizontal overflow is acceptable when paired with sticky key columns.
- Under 768px, collapse secondary columns, move filters into a drawer, and keep primary actions reachable from a sticky bottom bar.
- Tabs, segmented controls, and table/view toolbars must contain horizontal scrolling or wrap inside
  the component; they must never create page-level overflow at a 320 CSS px viewport.
- Do not use fluid typography; change structure at breakpoints instead.

## Data Tables

Tables are the primary product surface. Use AntD Table with server-side pagination, sorting, filtering, and row selection. Prefer virtualization for large datalakes.

Density:
- Default row height: 40px.
- Compact row height: 36px for operator-heavy screens.
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

### AntD Tokens

Map AntD through the Cryo tokens rather than styling each component ad hoc:
- `colorPrimary`: `--color-primary`
- `colorBgBase`: `--color-bg`
- `colorBgContainer`: `--color-surface-raised`
- `colorBorder`: `--color-border`
- `colorText`: `--color-ink`
- `colorTextSecondary`: `--color-muted`
- `borderRadius`: 8
- `fontFamily`: IBM Plex Sans/system stack
- `controlHeight`: 32, compact 28, large 36

Keep component vocabulary consistent across screens. Use AntD primitives for tables, forms, modals, drawers, tabs, menus, selects, date pickers, tooltips, and notifications.

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

Use 150-250ms transitions for hover, focus, selected rows, drawer/modal entry, filter chip changes, and status updates. Motion should convey state only. Use ease-out curves, avoid layout animation, and never orchestrate page-load sequences.

Respect reduced motion with instant transitions or simple opacity changes:

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
