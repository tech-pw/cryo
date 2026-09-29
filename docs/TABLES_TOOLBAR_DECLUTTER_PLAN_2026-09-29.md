# Tables Page Toolbar — Declutter Plan

Status: **plan only, no implementation**. Scope: `web/src/pages/catalog/TableExplorerSection.tsx` (1229 lines) and `web/src/components/FilterBar.tsx` (204 lines), rendered from `web/src/pages/Catalog.tsx` at route `/tables` (legacy `/catalogs` redirects here).

Same constraint as the Home plan: reuse existing tokens/primitives only. Every control here already uses shared components (`Button`, `Select`, `MultiSelect`, `Input`, `Segmented`, `Popover`) — this is a grouping and hierarchy problem, not a component-quality problem.

---

## 1. The problem

Before a single table row renders, the operator faces roughly **22-24 distinct interactive controls** stacked across three rows:

1. **Action row** (`TableExplorerSection.tsx:994-1076`): Savings view, Export N CSV, Refresh, "No grouping" select, Load a view select, Save view, Delete-view select, Set-default select, Columns menu.
2. **Search row** (`TableExplorerSection.tsx:1077-1106`): a natural-language "Find tables like..." input plus a Translate button.
3. **Filter row** (`FilterBar.tsx:56-181`): Select all matching / Clear selection, Schema-or-table input, Catalog multiselect, Preset multiselect, Grade multiselect, Priority multiselect, a Segmented control (All / Needs compaction / Healthy), an Advanced popover (itself containing 4 number inputs, a select, and a checkbox), and Reset.

DESIGN.md's own "Filters" section says: "Filters should feel like a query builder without exposing implementation detail." Right now every filter — common and rare — is exposed flat, at equal visual weight, all the time. That's implementation-detail exposure, not query-builder feel. It also violates PRODUCT.md's "show only useful information by default": six filter controls (Schema, Catalog, Preset, Grade, Priority, plus the segmented shortcut) are shown even though the segmented control (`All / Needs compaction / Healthy`) already covers the single most common filtering need in one click.

## 2. Recommendation — three-tier disclosure, not three flat rows

DESIGN.md already prescribes the fix in its own text, it's just not applied here: *"Put common filters in a compact toolbar... Advanced predicates live in a drawer or popover with readable chips."* Today's Advanced popover only holds the numeric threshold fields — the multiselects (Catalog, Preset, Grade, Priority) are sitting in the always-visible row when several of them are exactly the kind of secondary predicate DESIGN.md says belongs behind the popover.

**Tier 1 — always visible** (the actual common path: search + the one-click shortcut):
- The "Find tables like..." search input (already NL-capable, keep prominent — this is the primary interaction path per PRODUCT.md's "make filtering the primary interaction").
- The `All / Needs compaction / Healthy` segmented control — this alone answers the majority of "what do I look at" sessions.
- `Select all matching tables` — but only surfaced once a filter is active or a selection exists, not permanently docked at full width when the table is unfiltered and nothing is selected.

**Tier 2 — one compact filter bar, chip-based** (per DESIGN.md's "Filters" section: "Use chips for active filters, quick reset per chip"):
- Collapse Schema/Catalog/Preset/Grade/Priority into a single `Filters` button that opens a popover/drawer with those five controls grouped, and render **active** filters as removable chips inline in the toolbar (DESIGN.md's exact prescribed pattern). An operator with no filters set sees one `Filters` button and the segmented control — not five empty dropdowns.
- This is the single biggest visual-weight reduction: 5 always-rendered dropdowns → 1 button, chips appearing only when filters are actually active.

**Tier 3 — Advanced popover** (already exists, keep as-is): numeric thresholds stay here, this part is already correctly scoped per DESIGN.md.

**Action row** (Savings view / Export / Refresh / grouping / views / Columns): this row mixes *view management* (Load a view, Save view, Delete view, Set default — 3-4 controls) with *data actions* (Export, Refresh, Columns) and *display* (No grouping, Savings view toggle). Recommend splitting into:
- Left: the two or three actions used every session (Refresh, Columns).
- Right, grouped under a single `Views` menu/dropdown: Load/Save/Delete/Set-default view and grouping — these are configuration actions, not per-visit actions, and don't need permanent top-level buttons per DESIGN.md's "no dense chrome for its own sake."
- Export stays visible but could move next to Select-all/bulk-actions since it's usually a result of a filtered selection, not a standalone action.

## 3. Net effect

Rough control count in the always-visible toolbar drops from ~22-24 to roughly 8-10: search input, segmented control, one `Filters` button (+ chips when active), Refresh, Columns, a `Views` menu, Export. Everything else moves one click deeper — still reachable, not competing for attention on every page load. This is exactly DESIGN.md's own compact-toolbar-plus-drawer/popover model, applied consistently instead of partially.

## 4. Out of scope for this pass

- Any change to `FilterBar.tsx`'s underlying filter logic/state — this is a presentation regrouping only.
- Rebuilding the Advanced popover's numeric fields — already correctly scoped.
- The "Active tables" / match-count footer (`FilterBar.tsx:182-201`) — already appropriately understated, no change proposed.

## 5. Suggested implementation order

1. Move Catalog/Preset/Grade/Priority multiselects behind a `Filters` popover with chip rendering for active filters — biggest visual win, isolated to `FilterBar.tsx`.
2. Conditionally render `Select all matching tables` only when relevant (filter active or selection non-empty).
3. Consolidate view-management controls (Load/Save/Delete/Set default view, grouping) into one `Views` menu in `TableExplorerSection.tsx`.
4. Re-home Export next to bulk-selection actions.
