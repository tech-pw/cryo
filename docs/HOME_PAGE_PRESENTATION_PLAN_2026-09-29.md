# Home page presentation plan

> **Superseded** by [`HOME_PAGE_OVERHAUL_2026-10-18.md`](./HOME_PAGE_OVERHAUL_2026-10-18.md). The overhaul keeps the grade donut and a smaller platform gauge (with an always-on reason line) instead of the linear score bar and stacked grade bar proposed here.

Status: **plan only**. Scope: `web/src/pages/DatalakeHealth.tsx` (route `/`, title "Home").

This supersedes the thinner draft in `docs/HOME_PAGE_REDESIGN_2026-09-29.md`. That draft treated `74 CRITICAL` as "74 sits in a 0–39 band." It does not. The score and the word are two different signals. Getting that wrong would make the page more confident and still wrong.

The operator question stays the subtitle already on the page: **what happened last night, and is the lake worse?**

A person should answer that in about five seconds, without opening a popover.

---

## 1. What the page does today

Current order, top to bottom:

1. **Now strip** — three equal columns: Cryo vitals, last nightly run, reclaim yesterday.
2. **Platform pills** — scanner, holds, queue, engines, plus three pills that always say "Not recorded" (scheduler, governor, workers).
3. **What needs a person** — one tile, often `0`.
4. **What needs an engine** — a single "View holds" link, no count.
5. **What got worse** — two full-size tiles (tables at D/F, tracked tables) plus the grade bar.
6. **Health** — four equal tiles: size, small files, needs compaction, reclaimable storage.
7. **Table freshness** — the best chart on the page, buried.
8. **Recently discovered** — an unbounded card of fast-path findings, one padded box per table.
9. **Changed this week** — repeats the reclaim label and links to Cost → Impact.

Nothing is ranked. A finding and a denominator get the same card. The number the operator came for (bytes reclaimed) is replaced by the word `Partial`.

---

## 2. The story the page should tell

One column. Four beats. Size follows importance, not the order the queries were wired.

```
┌─────────────────────────────────────────────────────────────┐
│ 1. STATUS          the only large thing                      │
│    ✕  74 / 100   CRITICAL                                    │
│    [========74========|············]                         │
│    Why: success rate 71% · heartbeat ok                      │
│    Nightly 1,494/1,500 succeeded · finished 06:12 IST        │
│    Reclaimed yesterday   18.4 GB                             │
│    partial · 61 of 200 measured                              │
├─────────────────────────────────────────────────────────────┤
│ 2. NEEDS A PERSON  omitted when the count is zero            │
│    3 awaiting review                                         │
├─────────────────────────────────────────────────────────────┤
│ 3. IS THE LAKE WORSE?  small, one glance                     │
│    3.4k of 14.7k tables at D or F                            │
│    [F ▓][D ▓▓▓▓][C ▓▓][B ▓▓▓][A ▓▓]   worst on the left    │
│    Needs compaction  812    Reclaimable  1.2 TB              │
├─────────────────────────────────────────────────────────────┤
│ 4. WHERE          the freshness bars, already good           │
│    < 1h  ████████████  4.1k                                  │
│    1–24h ██████        2.0k                                  │
│    failed ██            180                                  │
├─────────────────────────────────────────────────────────────┤
│ 5. NEW TABLES     collapsed, 5 rows, worst first             │
│    Recently discovered · 23                    Show all →    │
│    sales.events_raw     warning   small files   2h           │
│    …                                                         │
└─────────────────────────────────────────────────────────────┘
```

Platform health (scanner, queue, engines) becomes one quiet line under the status block, not a second navigation. Pills that say "Not recorded" are hidden until the signal exists. A row of "Not recorded" teaches the operator to ignore the strip.

**Canvas.** The sidebar is 224px expanded, 56px collapsed (`web/src/components/shell/shell.css`). The top bar is 48px. Content padding is 24px above 1024px, 16px at 1024, 12px at 768 and below. There is no max-width on the home column. On `/` the page header, including the empty breadcrumb wrapper, is 106px before the first zone.

First screen of home content, expanded sidebar, below that header:

| Viewport | Inner width | Visible below the header |
|---|---|---|
| 1440×900 | 1168px | 722px |
| 1280×800 | 1008px | 622px |

Status, the lake sentence, and the top of the freshness card have to fit in 622px on a 1280×800 laptop. That is the budget. A tile wall does not fit. Zone gap stays 24px. Nothing in the status block grows to 24px type. That size is the page title.

---

## 3. Item by item

### 3.1 Cryo vitals — `74` and `CRITICAL` are not the same fact

**What the number is.** `cryo/reporting/vitals.py` starts at 100 and subtracts. A critical health-check hit is −20, a warning is −6, a stalled dispatch heartbeat is −25, a 24h success rate under 75% is −18, under 90% is −8. The score is clamped to 0–100. Slack already prints it as `CRITICAL — 74/100` (`CryoVitals` summary). The home page drops the `/100`.

**What the word is.** Status is not a band on that score. `CRITICAL` is forced if any health check is critical, the heartbeat is stalled, the work picture is severe, or the success rate is under 75% — even when the score is still 74. `DEGRADED` is the warning band of those same checks. `HEALTHY` means none of them fired. A 74 can be CRITICAL. A 60 with only warnings is DEGRADED. Painting 0–39 as "the critical zone" would be a lie.

**Why it feels unfinished.** The icon exists (`VitalsStatusIcon`: check, triangle, or X at 14px) but it sits beside a bare number. The reason is behind a popover. The operator has to already know the scale and already know to click.

**Show it like this.** A full-width stack, not a third of the Now grid and not a `Stat` tile. The score stays ink. Status color is only the icon and the status word. The bar is a fixed 0–100 scale with a notch, not a fill and not a gauge. Font is `var(--font-sans)` (IBM Plex Sans). Do not paste hex into `className`. Use the custom properties.

Reuse `Popover`, `Skeleton`, and the existing lucide icons. Keep `VitalsStatusIcon` local and change its size. The bar is local markup: a track, three segments, a notch. Do not use `Stat`, `Badge`, `Button`, `HealthGradeBar`, or a metric tile. Those bring a 24px metric, a tinted pill, or a card wash.

**Label.** Visible text is `Cryo`, not `Cryo vitals`. `Cryo` is this platform. `Lake` is the grade bar. 11px / 16px, weight 500, letter-spacing `0.015em`, `--color-muted`. Not uppercase. Not an `h2`. 13px is not a step on this scale.

**Icon.** Same row as the number, centered, 8px gap. Wrapper height 26px.

| Status | Icon | Size | Color |
|---|---|---|---|
| `CRITICAL` | `XCircle` | 20px (`size-5`) | `--color-status-danger` |
| `DEGRADED` | `AlertTriangle` | 14px | `--color-status-warning` |
| `HEALTHY` | `CheckCircle2` | 14px | `--color-status-success` |
| anything else | none | | |

The X is the only 20px icon on the page. Stroke only, no fill, no pill. `aria-hidden`. The word is the accessible name. No heart, flame, or illustration.

**Hero line.** All three pieces at 18px / 26px. Not `Stat`'s 24px. Tabular nums on the score and on `/ 100`. Gap 6px before the suffix, 12px before the word.

| Piece | Example | Weight | Color |
|---|---|---|---|
| Score | `74` | 600, tracking `-0.025em` | `--color-foreground` |
| Suffix | `/ 100` | 400 | `--color-muted` |
| Word | `CRITICAL` | 500 | status token |

Word color: `CRITICAL` → `--color-status-danger`. `DEGRADED` → `--color-status-warning`. `HEALTHY` → `--color-foreground`, not success. Do not render the API `headline` (`CRITICAL — 74/100`) as one string. A score of `0` is `0 / 100`, not an em dash. The number is never danger, even when the notch sits in the poor segment.

**Bar.** Only when the score is a finite number. Height 8px, full width. Track radius `--radius-sm` (6px), background `--color-chart-grid`. Not `bg-muted` — that utility is the text color `#5c667a`.

Three segments, always, including when the lake is quiet. This is a scale, not a progress fill. Flex grow `4 / 3 / 3` (about 40% / 30% / 30%) with a 2px gap, so a notch at the score lands in the band named below:

- 0–39, grow 4, `--grade-f`
- 40–69, grow 3, `--grade-d`
- 70–100, grow 3, `--grade-a`

A 74 sits in the `--grade-a` segment even when the word is `CRITICAL`. Do not recolor the segment under the notch. `--grade-f` and `--color-status-danger` share a primitive. Use the grade token on the scale and the status token on the word. Do not interchange them.

Notch: sibling of the track so the radius does not clip it. 2×12px, 2px above the track, `--color-foreground`, no shadow, no transition. `left` at the score percent, centered, clamped at 0 and 100. `aria-hidden`.

Range labels, one per segment, same split, not colored with grade tokens: `0–39`, `40–69`, `70–100`. 11px, weight 500, line-height 16px, `--color-muted`. Do not label them Poor, Critical, or Healthy.

Caption, next line: `Score position. Status is separate.` Same 11px / 16px / 500 / muted.

**Reason line.** Always visible when there is a real reason. 13px, weight 400, line-height 20px, `--color-foreground`. Not danger. No `Why:` prefix. Two lines max, then ellipsis. Full string in `title`.

Copy, in order. Do not invent a sentence.

- The deduction with the largest `points`. Tie: earliest in the array. Text is `reason` after the first `: ` if that prefix exists, otherwise the raw reason. Then ` (−N)` with U+2212. Points are 13px, weight 500, tabular nums, muted.
- If deductions are empty and status is `CRITICAL`, one line from the first payload field that applies: `Health checks critical (n)` from `health.critical_count`; else `dispatch_heartbeat.summary`; else `success_rate.summary`. If those fields are not on the client type, read them off the JSON or omit the line. Do not invent the sentence.
- `HEALTHY` and no deductions: omit the line. Do not print `No deductions` on the page.

**Popover.** Only when more than one deduction remains. Trigger is a local button, text `N more`, 12px, weight 500, `--color-link`, no border. Hover underline. It does not wrap the score. Content width 320px, padding 16px, title `Other deductions` at 12px weight 600. The reason line's deduction is not repeated. One deduction: no trigger.

**States.** Label `Cryo` stays. No notch, no segments, no status color, no `/ 100` unless a real score is on screen.

- Loading: skeleton 26×160px, then an 8px full-width bar. `aria-busy`. After 5 seconds, stop the pulse and show `Still loading…` at 13px muted. Do not tween a number.
- Error: `Vitals unavailable`, 14px / 22px, foreground, not danger. No bar. No retry in this block.
- Not measured: the words `Not measured` once, 18px / 26px, weight 500, muted. No icon, no suffix, no bar.

**Do not color.** The number, including 0. The `/ 100`. The label. The track, range labels, caption, and notch. The reason sentence. The block background — no red, amber, or green wash. The word `HEALTHY`. Iceberg blue on any part of this block. A filled badge. A radial gauge or a needle.

### 3.2 Reclaim yesterday — the number is the value, `Partial` is the caption

**Why it says Partial.** `reclaimYesterdayLabel` in `web/src/lib/nowStrip.ts` returns the bare word `Partial` when `measurement_state === "partial"` and either count is missing. On the live `GET /vitals` payload that is every partial day. `impact` carries `gb_freed_yesterday`, `direction`, `dod_pct`, and `wow_pct`. It does not carry `actions_with_bytes_evidence` or `actions_succeeded`. Those counts live on `north_star` (`byte_actions_measured`, `byte_actions_succeeded`), which this page never reads. The partial branch also never reads `gb_freed_yesterday`, so a partial day with a real byte total still prints `Partial`. The test in `nowStrip.test.ts` locks that in.

**Placement.** Not a third column and not a card. Stacked under the vitals reason, same left edge as the vitals bar. Kill `sm:grid-cols-3`. After the reason line: 8px, then the nightly clause, then 8px, then reclaim. Reclaim is one shrink-to-content row, `items-baseline`, 12px between the label and the number. The number stays next to the label, not pinned to the far edge. The caption is the next line, 2px down. If there is no caption, do not reserve the line.

**Label.** `Reclaimed yesterday`, not `Reclaim yesterday`. 12px / 16px, weight 550, `--color-muted-foreground`. IBM Plex Sans.

**Number.** Magnitude 18px / 26px, weight 600, `--color-foreground`, `tabular-nums`. Same step as the vitals score. Not 24px — that size is the page title. The unit from `formatGb` is smaller: 14px / 22px, weight 400, `--color-muted-foreground`, 4px to the left of the unit. Split on the last space so screen readers still hear `18.4 GB`.

**Caption.** 11px / 16px, weight 500, `--color-muted-foreground`. Not `--color-muted-subtle`. Pattern: `partial · 61 of 200 measured` (middle dot, `formatNumber` on both counts). Counts come from `north_star`, never from `impact`. Omit the `· N of M measured` tail if either count is missing, non-finite, or succeeded is 0. Do not print `0 of 0`. On a partial day the word `partial` still shows. Omit the whole caption when the state is not `partial`.

| State | Hero | Caption |
|---|---|---|
| complete, finite bytes, including 0 | `formatGb` (`0 GB`, `18.4 GB`) | none |
| complete, bytes null | `Not measured` | none |
| partial, finite bytes, including 0 | `formatGb` | `partial · 61 of 200 measured`, or `partial` if counts are absent |
| partial, bytes null | `—` (U+2014, not a hyphen) | `partial · bytes not reported` |
| no actions | `No actions` | none |
| unmeasured, empty, unknown, null | `Not measured` | none |
| loading | 26px skeleton, accessible name `Loading` | none |
| vitals error | `Unavailable` | none |

`Not measured` and `—` are 14px weight 400 muted. `No actions` and `Unavailable` are 14px weight 400 foreground. Unmeasured and no-actions ignore a 0 in `gb_freed_yesterday`. A finite partial 0 is a measured subtotal, so it is `0 GB` plus `partial`. The bare word `Partial` is the bug, not the target.

Never red. Never green. No icon, no `MetricTile`, no delta on this line. An incomplete run is not a failure.

### 3.3 Lake — a sentence, a bar, then a quiet inventory row

**What is wrong.** "Tables at grade D/F" is a finding. "Tracked tables" is the denominator. They render as peer `MetricTile`s, same height as the four Health tiles. The section title promises a change. Neither tile is a change.

No card, no h2, no `MetricTile`. The sentence is the heading. `aria-label="Lake"`. Outer margin is the page's 24px zone gap only. IBM Plex Sans.

**Sentence.** 14px / 22px. One flex row, baseline aligned, 8px gap, wrap allowed. Min-height 26px.

`3.4k` is the only emphasis: weight 600, `--color-foreground`, tabular nums. ` of 14.7k tables at D or F` is weight 400, `--color-muted`. The denominator stays muted, including its digits. D and F are not grade-colored.

When the D/F count is above 0, the whole sentence is one link to `/catalogs?grades=D,F`. Do not recolor it to link blue. At rest, underline only the count: 1px, `--color-link`, offset 3px. Hover underlines the whole sentence. No background, no lift. Focus-visible: 2px `--focus-ring-color`, offset 2px. Drop the "Fix in catalog" hint. At 0, same weights, no link, no underline, no success color.

**Delta.** Inline after the sentence, not inside the link. Render only when `GET /dashboard/fleet-trends` has a point for a day before today and the current D/F count is a number. Do not use `impact.dod_pct` for this chip. `0` on that prior point is a real zero. Delta is current D/F minus that point's `tables_at_grade_df`, an absolute count, never a percent. Delta `0`, a missing point, a failed fetch, or a null current count: render nothing. No dash, no `+0`.

Chip: height 20px, padding `0 6px`, pill radius, 1px `--color-border-default`, transparent background. 11px / 16px, weight 500, tabular nums. Worse: `+120 since yesterday`, `--color-status-danger`. Better: `−84 since yesterday` (U+2212), `--color-status-success`. Plain `formatNumber` under 10,000; compact at or above. Not a Badge wash. Not a link. It is smaller than the sentence, and it is not promoted into the status block beside reclaim, or it will be read as bytes.

**Hot subset.** The only other number worth adding on this line: how many of those D/F tables are hot. Same size as the delta, still smaller than the sentence. Copy `412 hot`, muted, not a second heading. Omit the clause if the cohort cannot be resolved. Query logs alone are not enough. A source with `query_log.mode == "iceberg_table"` writes `table_query_activity` and no `query_logs` rows. Ranking must fall back, or the count silently goes to zero. Not dollars. Not an importance average. Not a second list. The sentence already links to the catalog.

**Grade bar.** 8px tall, full width, radius `--radius-sm` (6px), track `--color-surface-sunken`. Not `bg-muted` — that token is text `#5c667a`. Overflow hidden. Segment gap 0. Order F, D, C, B, A, then any other grade with count above 0, then Not graded. Hide a zero count. Minimum segment width 2px. Colors: `--grade-f` through `--grade-a`. Not graded and non-A–F: `--color-muted-subtle`. No width transition. Do not replace this with a donut.

Legend 8px below the bar. Flex wrap, column-gap 16px, row-gap 4px. Dot 8×8, 6px before the label. Label 12px / 16px, weight 400, muted, tabular nums, full `toLocaleString()` (`F 1,234`). Not links.

The bar is `aria-hidden`. Segments are not focusable. An 8px target fails a 24px minimum. The legend is the labelled list. The sentence is the only grade link.

**Inventory.** Quiet inline row, not tiles. No border, background, shadow, or hover. Flex wrap, baseline, column-gap 24px, row-gap 4px. Each stat is label then value, gap 6px. No info icon, no provenance pill, no "Fix in catalog" line. Definitions stay on the label `title`.

Labels: 11px / 16px, weight 500, muted. Copy: `Data size`, `Small files`, `Needs compaction`, `Reclaimable now`. `Reclaimable now` is a stock. It is not yesterday's reclaim.

Values: 13px / 20px, weight 400, foreground, tabular nums. Size via `formatGb`. Small files compact. Needs compaction plain. Reclaimable via `formatBytesGb`. Null is `Not measured` in muted, never `0` or `0 GB`. A numeric 0 stays ink.

Emphasis only when needs-compaction is above 0: that stat is one link to `/catalogs?needsCompaction=1`. Label stays muted. Value is 13px / 20px, weight 600, `--color-link`, underline offset 2px. No wash, no stripe, no danger color. At 0 or null, no link and no emphasis.

**Rhythm.** Sentence, 8px, bar, 8px, legend, 12px, inventory. Section margin-bottom 24px. About 94px, against the current two 92px card rows.

**Empty and unknown.** Loading: a short line, an 8px bar, a legend line, an inventory line. No 88px card skeletons. Dashboard error: render nothing here. The page alert is the only error.

`grade_distribution` null: `Grade split unknown`, 14px / 22px, weight 400, muted. Not the word `Unknown` in the count slot. If tracked is known, append ` · 14.7k tracked` in that same muted run. No bar, no chip, no link. Inventory still renders.

Empty distribution and tracked `0`: `No tracked tables`, muted, no bar, no chip. Empty distribution and tracked null: same as unknown, not `0 of Not measured`. Empty distribution and tracked above 0: `0 of 14.7k tables at D or F`, count weight 600 ink, not a link. The bar is 100% Not graded.

D/F known, tracked null: `3.4k tables at D or F` or `0 tables at D or F`. No `of` clause. Link only if the count is above 0. Omit the Not graded segment.

Omit the section only when there is no dashboard payload. An unknown split still renders the muted sentence.

### 3.5 Recently discovered — five rows, then a sheet

**What is wrong.** `FastPathFindingsCard` maps every finding into its own bordered box: name, badge, type, reason, optional link, timestamp. No client cap. The fleet call sweeps at most 50 tables and each table can emit two findings, so the card can be 100 expanded rows. On a busy day that pushes freshness down the page.

**Header.** One 28px line, 16px horizontal padding, 1px `border-border` under it. Title `Recently discovered` is 14px / 20px, weight 600, `--font-sans`, `text-foreground`. Count is the same size, weight 500, `--color-muted`, tabular nums: ` · 23`. The count is inventory, never danger. Drop the subtitle from the card. Keep “Fast-path flags for new or barely-scanned tables. These are not auto-compacted.” as visually hidden text on the list, and as the sheet description. `Show all` sits on the right, 14px, weight 500, `--color-link` (`#266fa8`), no border, background, icon, or arrow. Underline on hover only. Render it only when the count is above 5.

**Rows.** Sort critical/error, then warning, then info, then newest `detected_at`. Show `min(5, count)` real rows. Do not pad to five. Do not expand inline to eight. Height 32px, one line, `nowrap`. 14px / 20px, 6px vertical padding. No bordered `li`, no `p-3`, no radius. Hairline `border-border` between rows. Hover is `--color-panel`, no lift.

Grid, 16px inset, 16px column gaps: `minmax(0,1fr) | 88px | 120px | 36px`.

| Column | Spec |
|---|---|
| `schema.table` | `--font-mono` 14px, weight 500, `--color-link`. Link to `/tables/{id}`. End ellipsis. Full name is the tooltip’s first line. |
| Severity | 8px dot (`h-2 w-2`, `aria-hidden`) plus the word. Word is `text-foreground`, weight 500, never truncated. `Critical` / `Warning` / `Info`. |
| Type | 14px, `--color-muted`. `small_files` → `Small files`. `missing_target_file_size` → `No target size`. Unknown types: underscores to spaces, ellipsis, full string in the tooltip. |
| Age | 12px, weight 500, `--color-muted`, tabular nums, right aligned. `now`, `14m`, `2h`, `3d`. Not `formatTime` — that string is about 110px and will not fit. |

**No Badge.** A filled pill is about 20px plus chrome on a 32px line, and the info variant reads as metadata while warning reads as a status chip. The word is the status. The dot is only a scan aid, same as the grade-bar legend. Dot tokens: danger `--color-status-danger`, warning `--color-status-warning`, info `--color-muted-subtle`. The word stays ink. Warning text `#a9791e` is about 3.9:1 on white, under the 4.5:1 bar for small text, so it must not be the word color. No success green. These rows are flags, not healthy tables.

**Reason and property link.** Reason is not a second line. Tooltip, max-width 320px: full name, reason, then `formatTime`. Also `aria-describedby` on the name link so focus opens it. Hover is not the only path. The property link exists only for `missing_target_file_size`, and it replaces the type cell. Label `No target size`, `--color-link`, `whitespace-nowrap`, href `/tables/{id}?tab=files#files-properties`, `aria-label="Open property recommendations for {name}"`. “Open property recommendations” stays in the tooltip.

**Show all.** A right-hand sheet, `sm:max-w-lg` (512px). The default sheet is `sm:max-w-sm` and will not hold these columns. Same row grammar, all findings, scroll. Not a second stack of cards. Catalog is the wrong destination: there is no findings filter, and one table can emit two findings. A catalog link would drop type, reason, and the property link.

**States.** Empty: header `· 0`, no `Show all`, current copy, no illustration, no blank rows. Loading: five 32px skeletons, not one `h-16`. Shimmer stops at 5 seconds, then “Still loading…”. No count until data arrives. Error: existing `QueryErrorAlert`, title “Failed to load fast-path findings”. Omit the count. Do not also show the empty state. The card is not a disclosure. Five rows stay visible whenever there are findings.

### 3.6 Page frame, platform line, and what needs a person

**Header.** Keep `PageHeader`. Title `Home`. Subtitle stays `What happened last night, and is the lake worse?` Do not pad it to 68–72 characters. No card, no actions, no crumbs on `/`. Existing chrome: 20px below, 16px padding under the title, 1px `--line` divider. Copy cap stays `72ch`. No page-column max-width. Width is the shell content padding (24px, 16px at ≤1024px, 12px at ≤768px). Background stays shell frost `--color-layout-bg`. Do not paint a background, radius, or shadow on the page root.

**Rhythm.** Four zones, in order: status, needs-person, lake, freshness plus discovered. `mb-6` (24px) on every zone except the last. Do not use `space-y-*` on the page root. Do not render an empty wrapper when a zone is omitted, or the gap doubles. No rules between zones. The header divider is the only page rule.

Surviving zone headings (lake, freshness, discovered) are 18px / 26px, weight 600, not `text-sm`. DESIGN.md writes 650, but that weight is not loaded and matches to 700, the same as the page title. Do not set 650. Status, platform, and needs-person have no visible heading. Inside a zone, 8px from the status content to the platform line, and 16px from freshness to discovered.

**Platform line.** Inside the status zone, not a second nav. 12px / 16px, weight 400, muted. No border, pill, background, or control height. Clauses separated by a muted middle dot. Each clause is a link: label muted, value foreground, underline on hover only. Not `text-primary` — a row of blue values reads as a filter bar.

| Clause | Show | Hide |
|---|---|---|
| Scanner → `/runs/scanner` | `Running` if a run is active; `Idle` if the payload exists and nothing is active; `Unavailable` on error, muted, still linked, not danger | pending, or no payload and not an error |
| Queue → `/runs` | `queue_depth` including `0` | null |
| Engines → `/runs` | `N configured`, including `0`; `Unavailable` on error | pending, or no payload and not an error |
| Scheduler, Governor, Workers | never | always. They are hardcoded `Not recorded`. |
| Holds pill | never | not this line |

If every clause is hidden, omit the nav.

**Needs a person.** One line, or nothing. No heading, no `MetricTile`, no icon. Render only when pending approvals succeeded and the length is above 0: a link to `/governance`, 14px, link color, `` `${n} awaiting review` ``, count in tabular nums. Omit when the length is 0, while loading, and on error. Do not show `0` or `Not recorded`.

Delete “What needs an engine” and the countless `View holds` link. This page has no hold count. Engine-load slots are not holds. Do not add a fetch in this pass. A counted holds clause can join the same line later. Until then the strip is approvals only.

**Skeleton.** Replace the inline ellipsis and the current 2-tile plus 4-tile skeleton. While vitals or nightly runs are pending, skeleton the status zone: 26px score line, 8px bar, 22px reason, 22px nightly, 26px reclaim, 16px caption, 16px platform. `aria-busy="true"`. Do not wait on scanner or engine load. While the dashboard is pending, skeleton the lake zone (one line, one bar, four short stats) and freshness (four rows), not metric cards. On dashboard error, render neither placeholder.

**Error.** One alert, directly under the page header, before status. Keep the existing error alert, title `Dashboard unavailable`, secondary Retry. Drop the duplicate `QueryErrorAlert` on the same dashboard error. Do not put vitals, scanner, engine, or approval failures in that alert. Status still renders beside a dashboard failure.

**Under 640px.** Status is already one column. Delete `sm:grid-cols-3`. Platform clauses wrap. No horizontal scroll, no sticky bar, no new breakpoint. Freshness labels stay 184px, and shrink to 128px below 640px.

### 3.7 Last nightly run — a clause, not a column

One line, 14px / 20px, weight 400, counts and clock in tabular nums. No column label, no icon, no card. It sits 8px under the vitals reason and 8px above reclaim.

The whole clause is one link to `/runs/{id}` when an href exists. Color is `var(--color-link)` (`#266fa8` light). Hover underline. Not `--color-primary` / `#277abe`, not the chart blue, not a grade color. `text-primary` is allowed only because `tokens.css` remaps it to `--color-link`.

Copy: `Nightly 1,494/1,500 succeeded · finished 06:12 IST`. The fraction is `batchCounts` succeeded/selected joined with `/`, no spaces. A missing count stays `Unknown`, never `0`. The clock is Asia/Kolkata, 24-hour `HH:mm`, then the literal `IST`. Do not use `formatTime` — that is the local zone and does not say IST. If the IST date is not today: `finished 28 Sep 06:12 IST`. `finished_at` null or invalid: `Not finished`, no IST, not danger.

Failed segment only when numeric `failed` is above 0, between the fraction and the time: `· 6 failed ·`. Only the digits use `--color-status-danger`. The word `failed` stays link color. Omit `· 0 failed`. A running, queued, partial, or unfinished healthy run is not red. That mistake already burned the fleet once (2026-09-21). The run status word is not in the clause.

No run: `No nightly run`, foreground, not a link. Loading: skeleton, accessible name `Loading`. Error: `Nightly run unavailable`, foreground, not danger.

### 3.8 Freshness chart — keep the card, tighten the rows

This is the one medium zone. Density comes from dropping the tiles around it, not from flattening the chart. Keep `Card`. Surface `--primitive-surface`, 1px `--color-border-default`, radius `--radius-lg` (12px), shadow `--shadow-card`. No hover lift. Padding 16px, 12px below 640px. Zone gap 24px above and below. Card margin 0, so that 24px is not stacked on the current `mb-4`. It sits after the lake block and before recently discovered.

**Title.** A zone `h2`, not `CardTitle`. 18px / 26px, weight 600, `--color-ink`. Not `text-sm`. Not weight 650. Subtitle 12px / 16px, weight 400, `--color-muted`, 4px under the title: `Latest snapshot age. Each bar opens those tables.` As-of on the title row, same 12px muted, tabular nums, 12px from the title block. Hide as-of when the chart is empty. 12px from the subtitle to the first row.

**Rows.** All buckets stay, including zeros, so the block does not jump. Row 28px, gap 4px. Bar 16px tall, radius 4px. Not `--radius-sm` (6px) — that pills a 16px bar. Track `--color-chart-grid` (`#e8ecf2` / `#202a38`), not `--color-surface-sunken` (in dark mode that token matches the card). Width is `count / max(count)`, not fleet share. Count 0: width 0, track only. Count above 0: minimum width 2px.

| Bucket | Token | Why |
|---|---|---|
| `within_24h` through `over_1y` | `--color-chart-primary` | A count mark. DESIGN.md allows iceberg blue here. It is not a grade. |
| `unknown` | `--color-ink-faint` | Missing data, not a warning. |
| `failed` | `--color-status-danger` | Same pixels as `--grade-f`. Use the danger token, not the grade token. |

No warning ramp. `--color-status-warning` is the same hex as `--grade-c`, and that bar sits directly above this chart. Stale is not a grade. The age is in the label. Green stays reserved for an improved delta.

Label column: 184px at 640px and up, 12px / 16px, weight 400, muted, no ellipsis. The longest label, "Updated in last 24 hours", fits. Below 640px: 128px, ellipsis, full string in the accessible name. That replaces the current 120px. Gap 12px on desktop, 8px on small.

**Count.** 14px / 20px, weight 600, `--color-ink`, tabular nums, width 48px (40px below 640px), right-aligned. Not `text-xs` and not weight 700. `14.7k` needs the 48px. The type scale has no 13px step, so this is 14px.

**Hover and focus.** Drop the fill opacity change. It points the wrong way. Hover: row wash `--color-primary-soft`, radius `--radius-sm`, instant. Label becomes `--color-link` with a 1px underline. Count and fill stay put. Focus: 2px solid `--focus-ring-color`, offset 2px. The row is the link. Values stay visible without hover.

**Table.** Keep `ChartDataTable`, collapsed. Bars already show label and count. The table is the only place Share (1 decimal) exists, because bars are scaled to the max bucket, not to 100%. Always open adds about 200px to the one medium block. Do not remove it. DESIGN.md requires the same values in a nearby keyboard-operable table. Toggle: `Counts and share`, 12px / 16px, weight 500, `--color-link`, 12px above it, native `details`. Caption stays visually hidden.

**Empty.** No dashed well, no icon, no sunken box. One left-aligned line, 14px / 20px: `No snapshot ages yet.` plus a text link `Run a catalog scan` to `/runs/scanner`, link color, underline on hover only.

### 3.9 Changed this week — a footer, not a third telling of reclaim

Once reclaim has a number in the status block, this section should not repeat it. One muted link: `Week-over-week reclaim is on Cost → Impact`. If that page does not actually show a week series, say `Open Cost → Impact` and do not promise a week view.

---

## 4. Charts that earn a place

| Question | Drawing | Do not draw |
|---|---|---|
| Is 74 bad, out of what? | Segmented bar, notch at the score, `74 / 100` | Radial gauge, pie, a lone letter |
| Why is the word CRITICAL? | One text reason, deductions on click | A second chart of deductions |
| How is the fleet split? | Existing grade bar | Donut |
| What got worse? | The D/F sentence. A sparkline only if a 7-day vitals series already exists | An invented trend |
| How stale? | Existing freshness bars | Area chart of the whole fleet |
| Which new tables? | Five text rows | Small multiples, a scatter |

A sparkline belongs next to the vitals score only when the API already has prior scores. A flat sparkline is omitted. Grafana drops the sparkline when the panel is too small; we should drop it when it says nothing.

No new chart library. The freshness bars are CSS. The grade bar already exists. A vitals bar is the same shape as `HealthGradeBar`, with a marker. Recharts is unnecessary on this page.

---

## 5. Color, type, and weight

Stay inside tokens already in the product. This plan does not add a palette. Font files are IBM Plex Sans 400/500/600/700 and IBM Plex Mono 400/500/600. There is no 550 and no 650. A declared 650 matches to 700, the same as the page title. Use 600 for zone headings and the vitals number. Use 500 for labels. Use 400 for body.

Where an earlier paragraph in this plan says 13px, use 14px (`text-sm`). 13px is not a step on this scale. The 11px micro label is the floor. Do not add 10px copy.

| Role | Size | Weight | Only allowed use |
|---|---|---|---|
| Page title | 24px / 32px | 700 (declared 650) | The one `Home` title. Do not restyle. |
| Zone heading | 18px / 26px | 600 | Lake, freshness, discovered. Not `text-sm`. Not `text-lg` (that is 18px / 28px). |
| Status and reclaim number | 18px / 26px | 600 | The score and the GB magnitude. Tabular nums. Not a heading. Not `Stat`'s 24px. |
| Body | 14px / 20px | 400 | Sentences, nightly clause, discovered rows. |
| Label, legend, badge word | 12px / 16px | 500 | Legends, platform line, severity words. |
| Micro label | 11px / 16px | 500 | `Cryo`, `Reclaimed yesterday`, inventory labels. |
| IDs | `--font-mono` 14px | 500 | Table names in the discovered list. Not headings. |

| Signal | Token | Also required |
|---|---|---|
| CRITICAL word and the X | `--color-status-danger` | The word `CRITICAL`. No card wash. |
| DEGRADED word and the triangle | `--color-status-warning` | The word `DEGRADED`. Not small text in this yellow. |
| HEALTHY check | `--color-status-success` on the 14px icon only | The word `HEALTHY` stays foreground. No green panel. |
| Score bar segments | `--grade-f`, `--grade-d`, `--grade-a` | Range labels stay muted. Do not swap these with status tokens even when the hex matches. |
| Grade bar segments | `--grade-a` through `--grade-f` | Legend text is muted. The dot carries the color. Grade B is olive `#4d7c0f`, not blue. |
| Partial / not measured | `--color-muted` | The word. Never red, never green. |
| Failed nightly digits | `--color-status-danger` | The word `failed` stays link color. |
| Worse D/F delta | `--color-status-danger` next to `+` | Better delta uses success next to `−`. More D/F tables is bad. Do not share one green with reclaim. |
| Links | `--color-link` (`#266fa8`) | `text-primary` is allowed only because it remaps here. `--color-primary` (`#277abe`) fails 4.5:1 on frost (4.33). Not small text. |

Contrast, computed the same way as `tokens.contrast.test.ts`. Small text needs 4.5:1.

| Pair | Ratio | Rule |
|---|---|---|
| `--color-muted` `#5c667a` on frost | 5.49 | Pass. |
| `--color-link` on frost | 5.09 | Pass. This is the link color. |
| `--color-primary` on frost | 4.33 | Fail. Not small text. |
| `--grade-c` / warning `#a9791e` on white | 3.86 | Fail. Swatch or icon only. The severity word stays ink. |
| `--grade-a` / success on white | 4.14 | Fail as a 12px word. Swatch or icon. |
| `--grade-d` on white | 4.14 | Fail as small text. |
| `--grade-f` / danger on white | 5.94 | Pass. Still not a wash. |
| `--color-muted-subtle` on frost | 2.87 | Fail. Not text. Dividers and info dots only. |

Legend pattern that is allowed: muted 12px text plus a grade-colored dot. Letter in the text, color in the swatch. Do not copy `Badge` `gradeB`. That class paints blue (`--color-on-tint` on a primary tint), not olive.

Motion: one entrance at most, 180ms opacity plus a 4px rise, or none. Budget is 320ms total, four sections max. Numbers do not tween. A bar draws once and does not replay on poll. Reduced motion already collapses this. Do not bypass it.

Do not: gradients, glass, side stripes, hover lift, donut, radial gauge, a green panel for healthy, a red wash behind vitals, grade B as blue, grade colors as status, status colors as grades, or `--color-primary` as link text.

---

## 6. What other products do, and what to take

Fetched this session from the product's own docs unless a gap is named. Ops dashboards: Datadog monitor summary and SLO widgets, Grafana stat and bar gauge, PagerDuty priority, Sentry issue priority and release health, GitHub security overview, Stripe balances, Linear triage, Vercel project overview. Lakehouse and observability homes are below.

### Lakehouse and data-quality homes

**Databricks Governance Hub Data** ([docs](https://docs.databricks.com/aws/en/admin/governance-hub/data), updated 2026-09-11) opens as equal tiles: catalogs, assets, tags, 30-day query volume, classification %, quality %. That is the inventory wall Cryo already has. The useful page is the drill-in: percent healthy of monitored tables, then a catalog list sorted most-unhealthy first, then an Unhealthy table whose columns are Status, First Detected, Reason (freshness or completeness), and Impact (High / Medium / Low). Healthy and Error are other tabs. Recently Resolved is its own section, so overnight self-heals are not mixed into the open list. Catalog Explorer's status words are Healthy, Unhealthy, Training, Error, Excluded, Not enabled. There is no 0–100.

**Snowflake Data Quality** ([dashboard](https://docs.snowflake.com/en/user-guide/data-quality-centralized-dashboard)) is the closest "what happened" page. Two cards only, with different denominators: healthy tables (percent of monitored tables with no open incident, bar of healthy vs unhealthy) and monitoring coverage (percent of all tables with at least one check, bar of monitored vs unmonitored). Then incident-type counts, then a preview of the most recent incidents, then schemas. Default window is 7 days. Trust Center is a security posture page, not a lake page. Its only useful idea is the delta vocabulary: newly opened, remediated, increased, decreased, unchanged. Default there is 28 days, which is the wrong grain for a morning page.

**Apache Amoro** ([quick start](https://amoro.apache.org/quick-start/), [metrics](https://amoro.apache.org/docs/latest/metrics/)) does not lead with a grade. Optimizing Status is a quota bar (`4 / 10`) and a process table: table, status word with a colored dot, duration, quota. History is a separate tab: SUCCESS or FAILED, start, finish, duration, input files, type (Minor). Failed is a first-class row. `table_summary_health_score` is a Prometheus gauge, not the screen, and the docs do not define its scale. Do not assume it is 0–100.

**Ryft** ([catalog](https://docs.ryft.io/lakehouse-management/catalog), [asset explorer](https://docs.ryft.io/lakehouse-management/asset-explorer)) has no score. The health signal is active storage as a percentage of total. A low ratio means history, unexpired snapshots, or bad retention. File-size distribution calls out small files and oversized files. Triage is three saved views: New Tables, Stale Tables, Unoptimized Tables. Total storage and table count are scale, kept visually apart from the active-storage percentage. Docs still describe the UI; the company was acquired by Cyera.

**Dremio Monitor** ([docs](https://docs.dremio.com/current/admin/monitoring/)) answers "what ran," not "is the lake worse." The only relative health number is the 24-hour job failure/cancel rate next to total jobs. No small-file or snapshot view. Arctic is not a separate console in these docs.

**Tabular** now redirects to Databricks. No live console. Do not invent a Tabular layout.

**Lakekeeper** ([maintenance docs](https://docs.lakekeeper.io/docs/nightly/table-maintenance/)) has no health dashboard. A run is success, skipped, or error. Skipped (encrypted, or `gc.enabled=false`) is not failed. Orphan runs report reclaimed bytes. There is no overnight summary. Steal the vocabulary only.

**OpenMetadata** Data Insights is an adoption dashboard (descriptions, owners, page views). That is the wrong home. The useful surface is Incident Manager: one incident per failing test, status New, severity word, Ack, history on the detail page. Not one row per retry.

**Monte Carlo** Table Health ([docs](https://docs.getmontecarlo.com/docs/using-the-table-health-dashboard)) is an inbox. The row circle is the worst status on that table, not an average. Their data-quality score (passing monitors on the last run over total monitors) uses a different formula from the daily trend (successful runs that day over runs that day). The docs warn the two diverge when monitors are hourly. Do not let Cryo's 0–100 and last night's job success share one formula. They already do not. The page must not look like they do.

**Bigeye** Issues ([docs](https://docs.bigeye.com/docs/view-issues)) is the triage home. Priority is an icon. The score is on hover. Default sort is highest priority. One issue threads later alerts on the same metric. The catalog is a different page.

**Anomalo** marketing says analysts should not stare at dashboards, and offers an AI insights feed. Do not copy that. Cryo is judged by whether optimize, expire, orphan cleanup, and manifest rewrite ran.

**AWS Glue Data Quality** has no account health home. A run is passed or failed, with the failing rule named. Lake Formation is permissions. Irrelevant here.

### Ops dashboards

- Datadog: a count and a denominator. Triggered means alert, warn, or no data. Missing data is not quiet. Cryo's "not scanned" freshness bucket stays visible, not folded into healthy.
- Grafana: a bar against a min/max. Empty is `—`, never 0. A radial gauge hides composition. Do not use one.
- GitHub security overview: named severity, then a short impact table, then view details.
- Sentry: a rate without the bad absolute beside it is useless. `74 / 100` without the reason is that mistake.
- PagerDuty: red and orange only for the two worst levels. Acknowledged is not triggered. A healthy queue waiting on workers is not failed.
- Stripe: pending and available are amounts, not the words Pending and Available.
- Linear: empty groups stay hidden. Vercel collapses the summary under one production deployment.

### Take, in read order

1. Last night is a status word plus a magnitude. Amoro's process row, Dremio's failure rate beside the job count. Reclaim is bytes. `Partial` is the caption.
2. Worse is a delta or an unhealthy list, not a stock. Snowflake's two bars have different denominators: healthy-of-monitored, and monitored-of-all. Tracked tables is the second caption, not a "what got worse" tile. Databricks' Unhealthy list (reason, impact) is the list under that line. Cryo does not have a yesterday delta yet, so the line is `3.4k of 14.7k at D or F` and nothing invented.
3. A short preview, worst first, one row per open problem. Snowflake's type counts plus View all. Bigeye's priority sort. OpenMetadata's one incident per check. Databricks' Recently Resolved stays collapsed, not mixed in.
4. The score is a word plus a reason, with `/100` beside the word. It is Cryo's own health. The grade bar is the lake. Label them `Cryo` and `Lake` so they cannot be read as one number.

### Do not take

- Equal-weight inventory tiles (Databricks Governance Hub, OpenMetadata Insights, Ryft total storage as a peer of active storage).
- A search box or an AI insights feed as the hero (Atlan, Anomalo).
- A monitor-type matrix or a 12-month SLA chart on `/` (Monte Carlo).
- Optimizer quota and resource gauges as the hero (Amoro).
- A radial gauge, a pie, a lone letter grade, or a green "all clear" illustration.
- Hiding an incomplete byte total behind the word Partial.

---

## 7. Data we can show now, and data we must not invent

**Show now, already on a payload this page fetches or can fetch**

- `score`, `status`, `deductions[]`, and `headline` (`CRITICAL — 74/100`). The page never reads `headline`.
- `impact.gb_freed_yesterday` on partial days. The formatter ignores it.
- Partial caption counts from `north_star.byte_actions_measured` and `byte_actions_succeeded`, not from `impact`.
- `impact.dod_pct` and `impact.wow_pct` when non-null. Null means "not a comparable total," not zero.
- Nightly `succeeded/selected`, plus `failed` from `batchCounts`, which the page ignores.
- Grade distribution and tracked table count.
- Freshness buckets.
- Fast-path findings. Cap in the client. The sweep is already limited to 50 tables.
- A 7-day score or D/F sparkline only by calling `GET /dashboard/fleet-trends`, which already returns `avg_health_score` and `tables_at_grade_df` by day. Do not add a backend for this. Do not draw the line if the series is empty.

**Do not draw**

- A delta when `dod_pct` is null and no prior grade count was returned.
- `0 GB` for an unmeasured or partial-without-bytes day.
- Scheduler, governor, and worker health. Nothing is fetched. The pills are hardcoded `Not recorded`. Hide them.
- Trino hot-cost dollars on this page. They are on the vitals JSON and are omitted from the score on purpose. Cost has its own page.

No new backend field is required for the presentation pass. The missing piece is the page not reading fields it already has.

### What else to add

Competitors put a lot above the fold. Almost all of it is already on this page, one click away, or a lie on Cryo. Two additions. Nothing else.

| Candidate | Verdict | Where | Why |
|---|---|---|---|
| Signed D/F count since yesterday | Add | Trailing clause on the lake sentence, 11px, smaller than the sentence | A stock of 3.4k does not mean worse. GitHub is the only cited page that puts a real period change above the fold. Use `tables_at_grade_df` from fleet-trends, not `impact.dod_pct`. Omit when yesterday is missing. Do not draw 0 as a change. |
| Hot tables at D or F | Add, if the cohort resolves | Next clause on that same line, same size as the delta | `3.4k of 14.7k` is mostly cold tables. Databricks, Monte Carlo, Bigeye, and GitHub all weight by impact, but a weighted list does not fit. The count is the reduction. Fall back to `table_query_activity`. Omit rather than show 0 from an empty query log. |
| Names of unhealthy tables, with reason | One click | The lake sentence already links to the catalog | A second list under discovered rows is a second inbox. Amoro's failed process rows belong on the run. |
| Monitoring coverage percent | Drop | — | The lake sentence already says tracked. A second percent will be read as the grade rate. Unscanned stays a freshness bucket. |
| Active storage as a percent of total | Drop | — | Snapshot buildup is already in the grade. The ratio sits too close to bytes freed yesterday. |
| Incident counts by type (optimize / expire / orphan) | Drop | — | Freshness is already a bar. The split can exceed table failures and will be read as a worse lake. |
| Write activity, query volume, table-count growth, tags, classification | Drop | — | Ingestion and catalog adoption. Not last night, not worse. |
| Engine quota, in-flight duration, queue depth as a hero | Drop | — | Queue depth is not failed jobs. Painting a healthy backlog as failed is how 5,952 of 6,117 jobs were recorded as failures on 2026-09-21. |
| Trino scan dollars, Cryo score sparkline, MTTR, ack counts | Drop | — | Scan cost is not reclaim. Cost is left out of the score on purpose. Ack is already the needs-a-person line, omitted at zero. |

Three byte facts must not share a line: active storage, reclaimable now, and bytes freed yesterday. A rewrite can move a lot and free nothing. `bytes_rewritten` is not `gb_freed_yesterday`. A null day-over-day percent is not flat. Freshness-failed is not grade F.

An audit of every unused field on the payloads this page already fetches found nothing else that earns a place. No new zone. Health-check counts, reclaim direction, the 24-hour job success rate, dispatch minutes-since, monthly cost, projected savings, top offenders, scan drift, schema cost, rewritten bytes, orphan file counts, engine slots, and SLA breach counts are either a second name for a number already on the page, a different population that will be misread as the same fact, or a stock that belongs on Cost or the run page. `avg_health_score` from fleet-trends is not the vitals score. Do not put it next to `74 / 100`.

The D/F delta chip has one extra guard. `health_trend[].tables_counted` is tables scanned that day, not fleet size. Omit a prior point whose `tables_counted` is not the same order of magnitude as today's graded count, or "+120" is a coverage change, not a worse lake.

The hot-at-D/F clause is the only addition that is not already a field. It is a count, not a new endpoint, and only if the cohort resolves from `table_query_activity` as well as `query_logs`. If it does not resolve, omit it. Do not show 0.

---

## 8. Size and position

The first screen is 800px below the 48px top bar, including the 24px content padding. On `/` the page header is 106px before the first zone. A 1280px viewport with the sidebar expanded (224px) leaves an inner width of 1008px, not 1232px. The home column has no max-width.

Zone gap is 24px (`mb-6`) on status, needs-person, and lake only. Freshness and discovered are one zone: 16px between them, not another 24px. Omit needs-person and its wrapper when the count is 0, or the gap doubles. The last zone has no bottom margin.

Heights below are the partial-day stack: one reason line, reclaim caption present, five discovered rows. Unspecified gaps are 0.

| Zone | Height | Width at inner 1008px | Must not grow |
|---|---|---|---|
| Status | 206px. Label 16, hero 26, track 8, ranges 16, caption 16, reason 20, 8, nightly 20, 8, reclaim 26, 2, caption 16, 8, platform 16. | Stack is 100%. Bar is the only full-width piece. Hero and reclaim shrink to content. Notch is 2×12 and is not in the sum. | Score and GB stay 18/26. Not 24px. |
| Needs a person | 20px, or 0 | Shrink to content. | No tile. |
| Lake | 98px. Sentence min-height 26, 8, bar 8, 8, legend 16, 12, inventory 20. | Bar is 1008px. Chip is 20px tall, inline. | Not a card. Sentence stays 14/22. Do not promote it to an 18px heading. |
| Freshness | 468px. Title 26, subtitle 16, 11 rows at 28px plus gaps, toggle 16, pad 32, border 2. | Label 184, gap 12, count 48, bar 730. | Card stays. Rows stay 28px. Bar stays 16px, radius 4px. |
| Discovered | 193px. Header 29, five 32px rows, four hairlines. | Name 684, then 88, 120, 36, gaps 16. | Rows stay 32px. No second line. |

On a 1280×800 laptop the status block starts about 120px down and is 206px, about 26% of the 800px screen. It is the tallest text block and shorter than the freshness card (468px, about 1:2.3). The fold cuts the freshness card around the eighth bar. Discovered starts below the fold. That is intentional. Do not shrink freshness to pull discovered up, and do not reorder.

At 768px the inner width is 744px. Heights do not change. The freshness bar shrinks to 466px. At 390px padding is 12px, inner width 366px. The lake zone grows to about 142px because the legend and inventory wrap. The freshness label becomes 128px and the bar 164px. Discovered name column ellipsizes. It does not overflow and it does not change the grid to widen the name. Platform clauses wrap. No horizontal scroll.

---

## 9. What this does to DESIGN.md

No new color, font, or chart library. Two clarifications, not a restyle:

1. A status word and a 0–100 score may disagree. The home page must show both, and must not map CRITICAL onto a fixed score band.
2. Equal-weight metric cards are the wrong default for this page. DESIGN.md already prefers restraint. This plan uses that: one dominant status, then lines.

If a later pass wants a dedicated "vitals notch" token, it can be a 2px foreground mark. It does not need a new color.

---

## 10. Build order, when this is approved

1. Fix `reclaimYesterdayLabel` so a partial day prints `gb_freed_yesterday` when it is a number, and the measured/succeeded caption from `north_star` when those counts exist. Update `nowStrip.test.ts`, which currently requires the bare word `Partial`. Smallest change, highest confusion removed.
2. Vitals line: `74 / 100`, status word, icon at 20px, reason line, segmented bar. Popover stays for the rest of the deductions.
3. Collapse the Now strip, the person/engine sections, and "what got worse" into the story in §2. Delete the empty headings.
4. Cap recently discovered at 5, one line each, worst first, `Show all`.
5. Relabel reclaimable storage. Shrink the health row. Move freshness up. Drop the repeated reclaim sentence.
6. Hide platform pills whose value is "Not recorded."

No new route is required for steps 1–3 and 5–6. Step 4 needs a destination for "Show all" — catalog filter if one exists, otherwise a drawer on this page. Confirm that before building the link.

---

## 11. Out of scope

- Implementing this plan in the same change.
- A fleet heatmap, a state timeline, or per-table sparklines on the home page. Those belong on the table page.
- Rewriting freshness into Recharts.
- A new illustration, empty-state graphic, or marketing hero.
- Closing the "since yesterday" gap by inventing a number.
