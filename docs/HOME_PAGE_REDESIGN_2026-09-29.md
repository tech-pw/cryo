# Home Page Redesign — Plan

Superseded by [`HOME_PAGE_PRESENTATION_PLAN_2026-09-29.md`](./HOME_PAGE_PRESENTATION_PLAN_2026-09-29.md). The score-band note in §2.1 of this draft is wrong: `CRITICAL` is not "74 is in the 0–39 range."

Status: **plan only, no implementation**. Scope: `web/src/pages/DatalakeHealth.tsx` (routes to `/`, exported as `DatalakeHealth`, 633 lines).

Constraint for every recommendation below: stay inside `DESIGN.md` / `PRODUCT.md` as written today. No new colors, no new typefaces, no gradients, no hero cards, no decorative motion beyond the one-time 320ms entrance already specced. Every fix here either reuses an existing token/component or asks for a genuinely new but on-system primitive (called out explicitly where that's the case).

---

## 1. The core problem with the page today

The page answers "what happened last night, and is the lake worse?" (its own subtitle) but it currently reads as a flat list of ten unranked sections of roughly equal visual weight: a three-column "Now" strip, a pill nav, four independent H2 sections ("What needs a person," "What needs an engine," "What got worse," "Health"), a big freshness card, a findings card, and a "Changed this week" footnote. Nothing on the page tells the operator *where to look first*. That's the opposite of PRODUCT.md's "show only useful information by default" and "operator speed over spectacle."

A home page for an on-call/operational tool should have one clear read order: **status → what needs me → what's trending wrong → supporting detail**. Right now the order is closer to *whatever order the queries happened to be wired up in*.

---

## 2. Item-by-item

### 2.1 "74 CRITICAL" — needs a scale, not just an icon

**Problem:** `74` with no visible max, and `CRITICAL` with no color-independent explanation of severity bands. `VitalsStatusIcon` already exists (CheckCircle2/AlertTriangle/XCircle by status) — good, that's DESIGN.md-compliant ("critical statuses should not rely on color alone"). The gap is that the icon communicates *category* but not *position on the scale*.

**Recommendation:** Render vitals as a fixed 0–100 scale with the current score as an emphasized tabular-nums number plus a compact horizontal band (reuse the exact `HealthGradeBar` segment pattern already built for grade distribution — same component shape: thin `h-2` bar, colored segments, 4.5:1-safe labels). Segments: CRITICAL (0–39, `--grade-f`), DEGRADED (40–69, `--grade-d` or `--grade-c`), HEALTHY (70–100, `--grade-a`). Place a small marker/notch at the current score. Label reads `74 / 100 · CRITICAL` so the number is legible with the word right next to it — never color alone, consistent with the existing icon-plus-text pattern in this same component.

This turns "74 CRITICAL" from an opaque number into "74 out of 100, deep in the critical band" at a glance — no new color, just the grade-bar pattern already in the codebase applied to a second metric.

### 2.2 "Reclaim yesterday: Partial" — should be a number

**Problem:** Confirmed in code (`DatalakeHealth.tsx:378-380`): the label is literally `reclaimLabel`, sourced from `reclaimYesterdayLabel(reclaimInputFromImpact(vitalsPayload?.impact))` in `lib/nowStrip.ts`. "Partial" is a *completeness* qualifier being shown instead of the *magnitude* — the operator wants to know how much was reclaimed, not whether the run was partial.

**Recommendation:** Show the quantity first, completeness second and de-emphasized: `18.4 TiB` as the dominant tabular-nums value (same visual treatment as `MetricTile`'s dominant value per DESIGN.md "Cards and Metrics"), with `Partial run` as a small muted caption underneath — same pattern the "Health" tiles already use for label/value/hint. If `nowStrip.ts`'s `reclaimYesterdayLabel` currently *only* returns a string like `"Partial"` with no numeric fallback, that's the actual bug: the underlying `reclaimInputFromImpact` data has a number, and the formatter is discarding it in favor of a status word. Fix in `lib/nowStrip.ts`, not by inventing new UI — this is a data-formatting fix wearing a design complaint.

### 2.3 "What got worse" card sizing

**Problem:** Confirmed in code (`DatalakeHealth.tsx:455-481`): "Tables at grade D/F" and "Tracked tables" render as two full `MetricTile`s inside a `MetricGrid columns={2}`, at the same visual weight as the four "Health" tiles below them. But "Tracked tables" (14.7k) isn't something that "got worse" — it's a denominator, not a finding. Only "Tables at grade D/F" (3.4k) is actually a "what got worse" signal.

**Recommendation:**
- Split the two tiles conceptually: **Tables at grade D/F** stays a full-weight `MetricTile` under "What got worse" (it's the actual finding).
- **Tracked tables** moves out of "What got worse" entirely — either fold it into the grade bar's own caption (e.g. "3.4k of 14.7k tracked tables at D/F") or move it to a small inline stat next to the section header, sized like the muted context/delta slot DESIGN.md already permits on `MetricTile` ("optional context/delta"), not a peer tile.
- This directly satisfies your "can be small in size" note — it's not that the card needs a generic size reduction, it's that a non-"worse" metric was sized as if it were one. Reuse the existing `MetricTile` with `emphasis` off and no card chrome, not a new smaller card variant.

### 2.4 "Recently discovered" — collapsible, capped preview

**Problem:** `FastPathFindingsCard` renders an unbounded `<ul>` of every finding returned by `getFastPathFindings` — confirmed no `.slice()` or pagination in the component (`DatalakeHealth.tsx:122-154`). On a busy day this card can push the whole page down indefinitely, which is the direct cause of "Health," the freshness chart, and everything else disappearing below the fold — the exact opposite of showing the most useful thing first.

**Recommendation:**
- Cap the inline list to 5 items, sorted by severity then recency (critical/error findings first — this is the "separate safety from decoration" principle: the worst finding should be first, not the newest).
- Below the 5, a single `Show all N →` link to a dedicated `/catalogs?findings=1`-style filtered view (or a drawer, per DESIGN.md's stated preference for drawers over pushing page-level content — "Drawers for inspection... because they preserve table context"). Do **not** build a client-side expand/collapse accordion for this; DESIGN.md explicitly prefers drawers for this kind of secondary-detail disclosure, and an inline accordion also fights "operator tables stay compact."
- Compact each row: current rows are 4 lines of text (link+badge+type / reason / timestamp) at full padding. Compress to two lines (title row with badge+type on one line, reason truncated to one line with `title=` tooltip for full text) — same information, roughly half the vertical footprint, still following the "no ambiguous status badges" rule since the badge variant logic already maps severity correctly.

### 2.5 Overall placement and story order

**Problem:** Current DOM order is: Now-strip (vitals/nightly/reclaim) → platform pill nav → "What needs a person" → "What needs an engine" → live-region-only summary → "What got worse" → "Health" → freshness card → "Recently discovered" → "Changed this week." Two issues:
1. "What needs an engine" (`DatalakeHealth.tsx:417-424`) is just a single "View holds" link with no count — it's the least-informative section on the page but sits ahead of the actual grade/health data.
2. The freshness chart and findings card — both genuinely rich, decision-relevant content — are the last things an operator sees, after scrolling past four lighter sections.

**Recommendation — reorder into four narrative zones**, each already-existing content just regrouped, matching your own framing (status → who needs a person → what's trending wrong → detail):

1. **Status** (unchanged position, tightened per 2.1/2.2): vitals scale, nightly run, reclaim-yesterday number, platform pill nav directly below since it's status-adjacent (scanner/queue/engines).
2. **Needs a person right now**: merge "What needs a person" and "What needs an engine" into one section — they're the same category (blocked-on-human work) and together they're currently two separate H2s for what is, in the screenshot, a single `0` and a link. One section, two compact stats side by side, using the existing `MetricGrid columns={2}`.
3. **What's trending wrong**: "What got worse" (tightened per 2.3) directly followed by "Health" tiles and the grade bar — this is the meat of "is the lake worse," so it moves up, ahead of the freshness chart.
4. **Detail / drill-down**: Table freshness chart, then "Recently discovered" (capped per 2.4), then "Changed this week" footer link to Cost → Impact.

This is a reordering + tightening pass, not a rewrite — every section keeps its existing query and component, just regrouped and resized by actual information value.

---

## 3. Visualization ideas within DESIGN.md's Recharts/no-decoration rules

DESIGN.md already mandates: Recharts, restrained colors, direct labels, every chart backed by an adjacent labelled table, never hover/color-only. Within that:

- **Vitals trend sparkline** (new, small): a thin 7-day sparkline next to the vitals score — "is CRITICAL new today or the fourth day running" is exactly the kind of context PRODUCT.md wants surfaced by default instead of requiring a click. Single muted line, one accent dot on the last point (reuses `--primitive-accent-500` per the existing "charts follow token" rule), no axis chrome — same restraint level as the freshness bars.
- **Grade distribution**: already good (the `HealthGradeBar` stacked bar is the right chart type for a categorical distribution — keep it, just also apply it to vitals per 2.1).
- **Freshness chart**: already good and already has its paired `ChartDataTable` per DESIGN.md's rule — no change needed structurally, just the reordering in 2.5.
- **Avoid**: donut/pie for grade distribution (explicitly banned as "decorative donut overload" in DESIGN.md) — the current stacked-bar choice is correct, don't regress it if this gets rebuilt.

---

## 4. Competitor / prior-art scan (brief, given time budget)

Looked at the general shape of ops/observability home dashboards in this class (Datadog, Grafana, Vercel, Linear's "My Issues"-style triage view) for pattern language only — not for visual borrowing, since PRODUCT.md explicitly scopes "polish and restraint of Linear, Stripe, Vercel light-mode" as the reference class already.

Patterns worth adopting (all expressible in existing tokens):
- **Severity-first ordering** (Datadog Monitors, Grafana alerting): worst-first sort is standard for any "what needs attention" list — directly supports 2.4's re-sort.
- **Single dominant status number with a scale beneath it**, not a bare number (Vercel's deployment health, Stripe's radar score) — supports 2.1.
- **"Since last check" delta framing** rather than a static snapshot — most ops dashboards pair a number with a small delta/trend indicator. Cryo's vitals and reclaim numbers are currently point-in-time only; a `+/-N since yesterday` caption (text, tabular-nums, no color-only signal) would need a backend delta if not already computed — flagging as a possible follow-up scope item, not in this pass since it may require an API change.

Patterns explicitly **not** adopted: card-grid KPI walls with identical-weight tiles (Vercel/Datadog homepages often do this) — PRODUCT.md's own anti-reference list bans "identical marketing card grids," and section 2.5's whole point is to break Cryo's page out of that shape, not lean into it further.

---

## 5. What's explicitly out of scope for this pass

- Any new color token, font, or motion beyond the existing 320ms/4-section entrance budget.
- The "since yesterday" delta framing (4.2) — needs a backend check first.
- Splitting `DatalakeHealth.tsx` into separate component files — a legitimate cleanup (per the earlier research: 633 lines, several inline components) but orthogonal to the visual/informational redesign; call out separately if wanted.
- Rewriting `lib/nowStrip.ts`'s formatters beyond the specific reclaim-yesterday fix in 2.2.

---

## 6. Suggested implementation order (once this plan is approved)

1. `lib/nowStrip.ts`: fix reclaim-yesterday to surface the number (2.2) — smallest, most isolated change.
2. Reorder sections per 2.5 — pure JSX reordering, no new components.
3. Trim "What got worse" tile weight per 2.3.
4. Cap + compact "Recently discovered" per 2.4 (need a `/catalogs?findings=1` or drawer target — confirm which before building).
5. Vitals scale bar + optional sparkline per 2.1/3 — the only genuinely new visual piece; do last since it's the most design-review-worthy addition.
