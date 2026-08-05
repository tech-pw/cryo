# Cryo UI/UX audit and research-backed path to 10/10

- **Audit date:** 2026-08-04
- **Audited revision:** `45d5376`
- **Scope:** Every current product route, including nested operational screens and hidden direct routes
- **Overall score:** **7.5/10** (dimension-weighted)
- **Perceptual quality score:** **7.4/10** (aesthetics, clarity, intuitiveness, and visualization)
- **Assessment type:** Expert heuristic review of current source, tests, and available visual baselines
- **Current evidence maturity:** **E1 overall**; selected E2 implementation properties; no E3+
  representative-user evidence

## Executive verdict

Cryo has moved beyond an early admin console. The current design system is coherent, dense without
being chaotic, and unusually transparent about maintenance plans, engine behavior, costs, failures,
and AI uncertainty. The best screens are already strong enterprise-operations interfaces:

1. **Job Detail — 8.8/10:** excellent diagnosis, evidence, prediction, SQL visibility, and guarded
   force-admission.
2. **Run Detail — 8.7/10:** clear live pipeline, progress, logs, jobs, history, and cancellation.
3. **Catalogs — 8.3/10:** excellent filtering, URL-backed state, bulk selection, comparison, export,
   and plan-preview workflow.

At the audited revision, the product was not yet 10/10 because five classes of risk remained:

- A write action labelled **Repair silent data loss** can run without the same confirmation/review
  protection used elsewhere.
- Accessibility automation disables color-contrast checks, skips when the API is absent, and does
  not cover most drill-downs, dialogs, drawers, or chart interactions.
- Several charts have no equivalent text/table view; SLA freshness bars are pointer-only.
- Search, Login Callback, Cost Attribution, and Skillsets do not yet meet the shared product
  standard for state handling, recovery, safety, and structured input.
- Broad pages such as Governance and Connections need stronger task-oriented grouping, URL-backed
  tabs, and form error summaries.

The fastest route to a defensible 9/10 is to fix safety and accessibility first, then strengthen
deep-linking, information architecture, and calibrated trust. A 10/10 score should be awarded only
after the acceptance tests in this document pass with representative operators, not merely after
the UI is restyled.

The numerical scores are design-quality estimates, not evidence-adjusted release claims. The
evidence-maturity model later in this document governs how strongly Cryo may claim those qualities
have been demonstrated.

### Implementation status in this branch

All safe, code-addressable recommendations in this audit are implemented without changing scores
before representative-operator evidence:

- **Shared system:** stronger Cryo identity and hierarchy; authenticated not-found recovery;
  URL-persistent tabs/filters/pagination; focused, field-linked form error summaries; restrained live
  status; contained mobile tabs/toolbars; and accessible chart-data equivalents. [R3–R5, R7, R9–R12,
  R16, R18–R20, R24, R27]
- **Authentication and search:** user-facing sign-in recovery, distinct OIDC callback outcomes,
  visible grouped search controls/counts, retry, live result status, same-route query synchronization,
  and URL-backed pagination. [R10, R11, R16, R19, R20]
- **Health, Catalog, and cost:** scoped health-to-Catalog links, de-duplicated KPIs, built-in/saved
  Catalog views and column visibility, shareable Catalog state, an exclusive snapshot-freshness
  distribution with failed-table isolation, keyboard and pointer drill-downs, full matching-set CSV
  export, corrected chart data keys, exact chart tables, URL-backed attribution month, semantic
  totals, and honest experimental/deep-link limits. [R2, R9, R11, R13–R15, R18, R24, R25]
- **Table Detail hierarchy:** four decision-critical metrics replace the former 13-item strip;
  secondary storage and metadata metrics use progressive disclosure; preset assignment and
  table-wide run controls each have one consistent home; findings are contained cards beside a
  dedicated health summary; and low-frequency ownership/override controls form a supporting layer.
  [R3–R6, R9–R12, R16, R18, R24]
- **Operational workflows:** factual not-run states, explained unavailable actions, URL-backed
  workflow state, richer scan review and schedule summaries, explicit simulation states, searchable
  and downloadable logs, restrained progress announcements, query detail inspection, SLA incident
  permalinks, and clearer recommendation confidence/benefit/action semantics. [R6, R10, R11, R16–R21,
  R25]
- **Governance, AI, Connections, and Skillsets:** task-oriented Governance navigation with active-only
  mounting, server-backed table search, evidence/type metadata, structured first-tier inputs,
  progressive connection setup/review, human-readable test results, secret-preserving edits, and
  confirmation/preview boundaries for Skillset activation and rollback. [R6, R8, R10–R12, R15, R17,
  R19, R21, R25]
- **Silent-data-loss safety:** the former repair claim is now an explicitly non-committing Spark
  inspection with exact evidence and limitations; the client sends `commit: false`, and no UI claims
  that manifests or files are changed. [R6, R8, R17, R21]
- **Automated evidence:** fixture-backed axe runs every canonical protected route in light and dark
  with contrast enabled, and every route has a 320 CSS px page-overflow gate. High-impact behavior,
  URL restoration, chart equivalents, forms, search, and recovery have focused regression tests.
  [R1–R11, R16, R17, R20, R24]

Items requiring contracts that do not exist remain deliberately unimplemented rather than faked:
cost-center Catalog predicates, streamed AI cancellation, authoritative model provenance/confidence,
silent-loss commit/rollback results, rich Skillset lifecycle/consumer history, and production UX
telemetry. Moderated operator studies, screen-reader sessions, comparative benchmarks, and
longitudinal field evidence also require real participants or deployed instrumentation.

The functional and perceptual baselines remain unchanged until the representative task targets later
in this document are met. Passing implementation checks demonstrates conformance to coded contracts;
it does not turn expert scores into validated user outcomes.

Verified implementation evidence on 2026-08-05: TypeScript and production build pass; the stylesheet
budget passes at 2,645/3,000 lines; 253 unit/component tests pass; 30 fixture-backed axe checks pass
across 15 canonical routes in light and dark with contrast enabled; and all 15 routes pass the 320 CSS
px page-overflow gate. A real-browser inspection verified the dark-theme shell and first-run
onboarding composition. Full data-page visual validation still requires a running Cryo API and
representative data; representative-user evidence remains E3+ work.

### Direct answer: is Cryo visually stunning and intuitive?

**Not yet.** Cryo is visually clean, restrained, credible, and professional. It looks like a
well-designed enterprise operations product, but it is not yet visually distinctive or memorable
enough to call “stunning.” The current light and dark baselines show consistent spacing, typography,
navigation, and component treatment, but also a very flat visual rhythm: repeated bordered cards,
small text, muted surfaces, limited focal contrast, and little product-specific visual identity.

- **Aesthetic appeal: 7.3/10.** Polished and coherent, but utilitarian rather than emotionally
  engaging or unmistakably Cryo.
- **Easy to understand at a glance: 7.7/10.** Strong headings, labels, grouping, and tables; weaker
  on broad/dense screens and when many same-weight cards compete for attention.
- **Intuitive to operate: 7.6/10.** Core table, run, and job workflows map well to operator tasks;
  hidden filters, local-only tab state, raw JSON, disabled controls without reasons, and a few
  pointer-only interactions reduce predictability.
- **Charts and visualizations: 7.0/10.** Generally purposeful and data-aware, with some excellent
  tables, waterfalls, pipelines, and labelled bars. They are not best-in-class because pie/donut
  charts are still used for comparison, several charts rely on hover or pointer input, chart/table
  equivalence is inconsistent, and uncertainty/benchmarks/annotations are limited.

This distinction matters: attractive interfaces are often *perceived* as easier to use, even when
task performance has not improved—the aesthetic-usability effect. Visual refinement should
therefore strengthen hierarchy and comprehension, not decorate over workflow problems. [R26, R29]

## Evidence and limitations

### Evidence used

- Canonical routes and aliases in [`web/src/App.tsx`](../web/src/App.tsx).
- Current navigation, shell, tokens, responsive behavior, and shared primitives.
- Source review of every routed page, its nested tabs, forms, tables, charts, actions, loading
  states, errors, and empty states.
- Current Playwright accessibility coverage in
  [`web/e2e/accessibility.spec.ts`](../web/e2e/accessibility.spec.ts).
- Current light/dark visual baselines for Login, Health, and Operations in
  [`web/e2e/visual/`](../web/e2e/visual/).
- Existing unit, integration, smoke, and critical-path tests as implementation evidence.
- The primary standards, peer-reviewed papers, and authoritative design research in [R1–R56].

### Runtime limitation

A fresh local UI/API session could not be launched during the audit because the application runtime
and local data services were unavailable. Frontend dependencies were subsequently restored with the
repository's existing Node.js runtime, enabling unit, type, style-budget, and production-build
validation of the first implementation phase. No score in this audit pretends to include a live
usability session. Scores are therefore accompanied by evidence confidence:

- **High:** current screenshot baseline plus current source.
- **Medium:** current source plus repository test evidence.
- **Low:** source only, hidden surface, or behavior that depends heavily on runtime data.

This is not a WCAG conformance statement and not a substitute for task-based research with real
operators.

The July 2026 audits were read as historical context only. Their scores were not reused because the
current redesign has materially changed navigation, tables, tabs, state handling, and several
previously incomplete pages.

## Scoring model

Every surface is scored from 0 to 10 on the same eight dimensions.

| Dimension | Weight | What 10/10 requires |
|---|---:|---|
| Visual hierarchy and consistency | 10% | One clear primary task, consistent hierarchy and tokens, no redundant or competing emphasis. |
| Information architecture and discoverability | 15% | Role-appropriate grouping, obvious location and next step, stable URLs for meaningful views, and no hidden essential surface. |
| Task efficiency and workflow fit | 20% | Core operator tasks are fast, batch-safe, preserve context, and minimize recall and unnecessary decisions. |
| Feedback, loading, empty, and error states | 10% | Every wait and outcome is visible, actionable, correctly announced, and proportional to duration. |
| Error prevention, trust, and transparency | 15% | High-impact actions expose scope and stakes, are reviewable/reversible where possible, and automated output shows evidence and uncertainty. |
| Accessibility | 15% | Verified WCAG 2.2 AA, full keyboard and screen-reader operation, non-color cues, chart alternatives, visible focus, and adequate targets. |
| Responsive and adaptive behavior | 5% | Non-tabular content reflows at 320 CSS px; tables/diagrams are contained; controls remain usable at zoom and touch sizes. |
| Data and decision clarity | 10% | Correct chart encodings, explicit units/time windows/precision/provenance, and a clear decision or action path. |

The weighted score is:

`Σ(dimension score × dimension weight)`

Scores are expert judgments anchored to observable implementation. They are intentionally not more
precise than one decimal place.

## Route coverage

| Product surface | Canonical route | Confidence | Scored separately |
|---|---|---|---|
| Login | unauthenticated protected route | High | Yes |
| Login Callback | `/login/callback` | Low | Yes |
| Datalake Health | `/health` | High | Yes |
| Catalog Dashboard + Table Explorer | `/catalogs` | Medium | Yes |
| Table Detail | `/tables/:tableId` | Medium | Yes |
| Cost | `/cost` | Medium | Yes; all three tabs reviewed together |
| Cost Attribution | `/attribution` | Low | Yes; hidden direct route |
| Operations | `/operations` | High | Yes; Workflows and All Runs reviewed together |
| Scanner workflow | `/operations/scanner` | Medium | Yes |
| Preset workflow | `/operations/presets/:presetId` | Medium | Yes |
| Run Detail | `/operations/runs/:runId` | Medium | Yes |
| Job Detail | `/jobs/:jobId` | Medium | Yes |
| Smart Maintenance | `/smart-maintenance` | Medium | Yes |
| Intelligence | `/ai` | Medium | Yes |
| Governance | `/governance` | Medium | Yes; all four task areas reviewed together |
| Query Performance | `/queries` | Medium | Yes |
| SLA Monitoring | `/sla` | Medium | Yes |
| Search Results | `/search` | Medium | Yes |
| Connections | `/connections` | Medium | Yes; all connection types and drawers reviewed |
| AI Skillsets | `/skillsets` | Medium | Yes; admin-only |

Redirect-only aliases are not independent interfaces and are not double-counted: `/`, `/tables`,
`/scans`, `/runs`, `/presets`, `/optimize`, `/cost/aws`, `/aws`, `/scans/:runId`, and
`/runs/:runId`. The development-only `/dev/styleguide` is reviewed as a design-system aid, not as a
product page.

## Full scorecard

**Abbreviations:** VH = visual hierarchy; IA = information architecture; Task = task efficiency;
States = feedback/loading/empty/error; Trust = error prevention/trust; A11y = accessibility;
Resp = responsive/adaptive; Data = data/decision clarity.

| Surface | VH | IA | Task | States | Trust | A11y | Resp | Data | Mean | Weighted |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Login | 8 | 8 | 9 | 8 | 8 | 8 | 8 | 7 | 8.0 | **8.1** |
| Login Callback | 6 | 5 | 6 | 6 | 4 | 7 | 8 | 4 | 5.8 | **5.6** |
| Datalake Health | 8 | 8 | 8 | 8 | 8 | 7 | 8 | 8 | 7.9 | **7.9** |
| Catalogs | 9 | 9 | 9 | 8 | 8 | 7 | 7 | 9 | 8.2 | **8.3** |
| Table Detail | 8 | 8 | 8 | 8 | 5 | 7 | 7 | 9 | 7.5 | **7.5** |
| Cost | 9 | 8 | 8 | 8 | 8 | 7 | 7 | 9 | 8.0 | **8.0** |
| Cost Attribution | 7 | 6 | 7 | 7 | 6 | 6 | 7 | 5 | 6.4 | **6.3** |
| Operations | 8 | 8 | 8 | 8 | 7 | 7 | 8 | 8 | 7.8 | **7.7** |
| Scanner workflow | 8 | 7 | 8 | 8 | 8 | 7 | 8 | 8 | 7.8 | **7.7** |
| Preset workflow | 8 | 7 | 8 | 8 | 8 | 7 | 8 | 8 | 7.8 | **7.7** |
| Run Detail | 9 | 9 | 9 | 9 | 9 | 7 | 8 | 9 | 8.6 | **8.7** |
| Job Detail | 9 | 9 | 9 | 9 | 9 | 8 | 8 | 9 | 8.8 | **8.8** |
| Smart Maintenance | 8 | 8 | 8 | 8 | 7 | 7 | 8 | 8 | 7.8 | **7.7** |
| Intelligence | 8 | 8 | 8 | 8 | 7 | 7 | 7 | 8 | 7.6 | **7.6** |
| Governance | 7 | 6 | 7 | 7 | 7 | 6 | 6 | 8 | 6.8 | **6.8** |
| Query Performance | 8 | 8 | 8 | 8 | 8 | 6 | 7 | 9 | 7.8 | **7.8** |
| SLA Monitoring | 8 | 8 | 9 | 8 | 8 | 5 | 7 | 9 | 7.8 | **7.8** |
| Search Results | 6 | 6 | 7 | 5 | 6 | 5 | 7 | 7 | 6.1 | **6.1** |
| Connections | 8 | 8 | 8 | 8 | 9 | 7 | 7 | 8 | 7.9 | **7.9** |
| AI Skillsets | 8 | 7 | 7 | 7 | 5 | 5 | 7 | 7 | 6.6 | **6.5** |
| **Dimension average** | **7.9** | **7.6** | **8.0** | **7.7** | **7.3** | **6.7** | **7.4** | **7.9** | **7.5** | **7.5** |

The biggest cross-product weakness is accessibility verification, not visual polish. The largest
gap between a strong current screen and 10/10 is usually proof: keyboard behavior, screen-reader
announcements, contrast, reflow, destructive-action review, and task success have not yet been
verified end to end.

## Perceptual-quality scorecard

The functional scorecard above asks whether the product works safely and effectively. This
supplement answers the user's separate perceptual questions:

- **Aesthetic appeal:** polish, balance, visual rhythm, emotional quality, and distinctiveness.
- **At-a-glance clarity:** whether a first look reveals page purpose, current state, and primary
  action.
- **Interaction intuitiveness:** whether labels, affordances, sequence, and outcomes match operator
  expectations without instruction.
- **Visualization quality:** whether tables, charts, pipelines, and status graphics use an
  appropriate encoding and support accurate decisions.

These are equally weighted within this supplemental score. A dash means the page contains no
decision-relevant data visualization; its perceptual mean excludes that dimension. Scores for
Login, Health, and Operations include current light/dark visual baselines. Other pages retain the
evidence-confidence limitations above.

| Surface | Aesthetic appeal | At-a-glance clarity | Intuitiveness | Visualization quality | Perceptual mean |
|---|---:|---:|---:|---:|---:|
| Login | 7 | 8.5 | 8.5 | — | **8.0** |
| Login Callback | 5.5 | 6 | 6 | — | **5.8** |
| Datalake Health | 7.2 | 8 | 8 | 6.5 | **7.4** |
| Catalogs | 7.5 | 8.5 | 8.5 | 8 | **8.1** |
| Table Detail | 7.8 | 8 | 8 | 7 | **7.7** |
| Cost | 8 | 8 | 8 | 8 | **8.0** |
| Cost Attribution | 6.8 | 7 | 7 | 5.5 | **6.6** |
| Operations | 7.5 | 8 | 8 | 6.5 | **7.5** |
| Scanner workflow | 7.5 | 7.5 | 7.5 | 7 | **7.4** |
| Preset workflow | 7.5 | 7.5 | 7.5 | 7 | **7.4** |
| Run Detail | 8 | 8.5 | 8.5 | 8 | **8.2** |
| Job Detail | 8 | 8.5 | 8.5 | 8 | **8.2** |
| Smart Maintenance | 7.5 | 8 | 7.5 | 7 | **7.5** |
| Intelligence | 7.5 | 7.5 | 7.5 | 7 | **7.4** |
| Governance | 7 | 6.5 | 6.5 | 6.5 | **6.6** |
| Query Performance | 7.5 | 8 | 8 | 7.5 | **7.8** |
| SLA Monitoring | 7.5 | 8 | 8 | 6 | **7.4** |
| Search Results | 6.5 | 6.5 | 7 | — | **6.7** |
| Connections | 7.5 | 8 | 7.5 | — | **7.7** |
| AI Skillsets | 7 | 7 | 6.5 | — | **6.8** |
| **Dimension average** | **7.3** | **7.7** | **7.6** | **7.0** | **7.4** |

### Why the aesthetic score is 7.3 rather than 9–10

**What works**

- The dark sidebar, quiet neutral surfaces, restrained blue accent, IBM Plex typography, compact
  density, consistent radii, and coherent light/dark themes create a serious enterprise tone.
- The shell and primary content have stable alignment. Controls and status treatments repeat
  predictably, producing visual trust through consistency.
- Health and Operations pass a basic “squint test”: navigation, title, top actions, summary, and
  detailed content remain recognizable as groups. Clear hierarchy and grouping improve
  comprehension, not just beauty. [R27, R28]

**What prevents “visually stunning”**

- Too many equal-weight white/near-black cards create a tiled, repetitive rhythm. Borders do much
  of the grouping work, while whitespace, scale, and background depth do too little.
- The typography range is narrow and often small. Page title, section title, card title, label, and
  table text do not always create a strong enough scan path.
- The product has little ownable visual identity beyond its name and blue accent. A screenshot is
  competent but could belong to many infrastructure dashboards.
- Empty first-run screens correctly explain next steps but visually dominate current baselines;
  they do not demonstrate the product’s eventual data story or create a memorable first
  impression. People form stable visual-appeal judgments in as little as 50 ms, so the first
  viewport matters. [R30]
- Dense screens prioritize information coverage over compositional focus. That is appropriate for
  expert work, but a 10/10 interface still needs one unmistakable visual and operational priority.
  [R27, R28]

### Why clarity and intuitiveness are good but not exceptional

**What works**

- Labels use the domain vocabulary operators expect; breadcrumbs, tabs, semantic tables, page
  actions, and status badges make most core workflows recognizable.
- Catalogs, Run Detail, and Job Detail strongly support recognition over recall by keeping scope,
  state, evidence, and next actions visible. [R11, R13]
- Consistent primitives make learned behavior transferable across pages.

**What holds the scores below 9**

- Governance places too many specialist jobs at one level; Search hides a supported group filter;
  Cost Attribution is itself hidden; workflow tabs reset; some advanced tasks begin with raw JSON.
  These increase choice, recall, and uncertainty. [R11, R12, R15, R22]
- Repeated cards and metrics can make “what should I do now?” less obvious than “what data is
  available?”
- Some affordances are weak or misleading: disabled New Preset lacks a reason, no-run workflows can
  appear queued, SLA bars look like data but are also controls, and generic links lose selected
  context.
- Intuitiveness must ultimately be demonstrated through first-click and task-success research, not
  inferred only from orderly source code. The aesthetic-usability effect can inflate users'
  subjective ease ratings, so observe behavior as well as stated preference. [R26, R29]

### Why chart and visualization quality is 7.0

**Strong patterns**

- Catalogs uses a table for multivariate comparison and bulk action rather than replacing
  operational data with cards. [R13]
- Cost's waterfall maps current cost through operation-specific savings to the projected result,
  which is more explanatory than a generic trend line.
- Run/Scanner/Preset pipelines match process sequence; labelled histograms and table-linked
  summaries often support operational triage.
- Many visualizations include explicit values, tooltips, empty states, and adjacent lists.

**Weak patterns**

- Cost Attribution uses a pie chart for team comparison; storage and savings also use donuts.
  Angle/area judgments are less accurate than position along a common scale, so sorted bars or dot
  plots are better for comparison. [R24, R31]
- SLA's freshness bars are clickable only through pointer events. Other Recharts views depend on
  hover for detail and do not consistently expose a keyboard/text equivalent. [R4, R5, R9]
- Several charts lack a nearby complete data table, direct labels, annotations, benchmarks,
  uncertainty bands, or a clearly stated decision question.
- Color palettes are coherent, but status/severity and selection can still carry too much meaning
  through hue. [R2]
- Small fixed chart heights and hidden axis labels can make dense categories hard to interpret.

## Research-backed visual enhancement plan

### A. Make Cryo distinctive without making it decorative

1. Define a product-specific visual concept around **cold control, layers, and flow**: a restrained
   glacial blue/teal data palette, subtle depth for active operational layers, and a consistent
   visual motif for source → scan → plan → execute → verify. Apply it to pipelines, illustrations,
   and key states—not every surface. Distinctiveness should preserve contrast and status semantics.
   [R1, R2, R27]
2. Establish three deliberate elevation/grouping levels: page canvas, task section, and raised
   action/inspection surface. Remove borders where proximity and whitespace already establish a
   group; too many containers add clutter. [R27, R28]
3. Use a controlled three-level type scale and stronger numeric typography. Give the page's
   decision, critical state, or primary KPI the largest scale; do not let every metric compete.
   [R27, R28]
4. Create one purposeful “hero decision” region on dashboard pages: **current state → why it
   matters → recommended action**. Aesthetics should direct attention, not add ornament. [R14,
   R27]
5. Design polished populated, empty, degraded, and learning-state compositions for visual
   regression. The first viewport must communicate Cryo's value even before production data is
   available. [R20, R30]

### B. Improve at-a-glance understanding

1. Apply a blur/squint review to every page: title/purpose, current state, and primary action must
   remain the three dominant groups. Reduce contrast or remove containers from supporting content
   until that order is clear. [R27, R28]
2. Replace repeated KPI walls with a decision hierarchy: one primary outcome, two to four
   diagnostic measures, then detail. Dashboards should answer a defined question rather than merely
   display available numbers. [R14, R15]
3. Use sentence-style insight titles that state the finding—“12 tables exceed the small-file
   threshold”—instead of generic labels such as “Overview” where the data permits it. Keep the
   metric definition and timeframe nearby. [R11, R14]
4. Preserve selected context in URLs and links so the visual state matches what users expect after
   refresh, back, or drill-down. [R11, R18, R21]
5. Progressive-disclose raw SQL, JSON, lineage internals, and model telemetry after the decision
   summary rather than making expert detail compete with the primary task. [R12, R15, R22]

### C. Make interactions feel self-evident

1. Make the whole affordance visible: interactive charts need focusable controls or explicit
   filter buttons, disabled actions need a reason, and rows that navigate need a consistent link or
   disclosure treatment. [R4, R5, R11]
2. Standardize action hierarchy across pages: one primary action, neutral secondary actions,
   dangerous actions separated and named by consequence. Review screens should state scope,
   effect, and recovery. [R6, R17, R21]
3. Prefer visible choices and structured controls over remembered syntax or raw JSON. Offer presets
   and examples, with advanced source mode as progressive disclosure. [R10–R12, R15]
4. Test first-click comprehension for the top tasks. Target at least 90% correct first action for
   finding an offender, previewing maintenance, diagnosing a failure, filtering an SLA breach, and
   configuring a connection. Measure behavior separately from visual-attractiveness ratings.
   [R26, R29]

### D. Upgrade charts to best-in-class decision tools

Use the following default encoding policy:

| Decision | Preferred visualization | Avoid/default caution | Research basis |
|---|---|---|---|
| Compare teams, tables, operations | Sorted horizontal bar or dot plot on a common scale | Pie/donut for precise comparison | [R24, R31] |
| Change over time | Line/area with direct end labels, target/reference line, and anomaly annotation | Unlabelled sparklines as the only evidence | [R14, R24] |
| Distribution | Histogram, box plot, or quantile bands with explicit bucket definitions | Averages without spread | [R24, R31] |
| Part-to-whole | 100% stacked bar for a small stable set plus exact values | Multiple similar-colored slices | [R2, R24] |
| Process/progress | Step pipeline with current, complete, failed, skipped, and blocked states | Decorative flow diagrams without state | [R11, R21] |
| Forecast/AI estimate | Line/range or point/interval with confidence, assumptions, and provenance | Single precise-looking number | [R25] |
| Multivariate operations | Semantic table with sorting/filtering and optional in-cell bars | Card grids that block comparison | [R13] |

For every chart:

1. Write the decision question in the title and include timeframe, unit, source freshness, and
   comparison baseline. [R11, R14]
2. Direct-label important series/points and annotate threshold crossings, anomalies, and operation
   effects. Keep gridlines quiet but visible; experimental replications show chart size, gridline
   contrast, and encoding choice affect judgment accuracy. [R24, R31]
3. Provide a complete nearby table/list and CSV export; all chart-triggered filters must also be
   available as keyboard controls. [R4, R9, R13]
4. Never encode critical meaning only by color. Add text, shape, pattern, position, or icon, and
   test both themes and common color-vision deficiencies. [R1, R2]
5. Show uncertainty honestly: ranges, sample size, model type, last-updated time, and whether a
   value is measured, estimated, heuristic, or learned. [R25]
6. Validate charts with task questions—identify maximum, compare two values, detect threshold,
   explain trend, choose action—and require at least 90% accuracy before calling them best-in-class.
   [R24, R31]

## Shared experience

### What is already strong

- Responsive shell, mobile navigation, command palette, global catalog search, theme switching,
  skip link, and semantic `<main>`.
- One visible page `<h1>` through the shared `PageHeader` on nearly every page.
- Shared loading, retryable error, empty-state, table, tab, dialog, and confirmation primitives.
- Data tables support sorting, selection, pagination, density, responsive containment, and useful
  empty copy.
- Dangerous actions commonly use an accessible alert dialog with explicit stakes.
- Statuses generally pair icons/text with color.
- IBM Plex fonts are local, focus tokens are centralized, and reduced-motion preferences are
  respected.
- Plans, SQL, estimates, confidence, evidence, and failure explanations are exposed rather than
  hidden behind automation.

### Shared path to 10/10

1. Make the accessibility gate deterministic with fixture-backed pages; enable color contrast and
   cover every canonical route, meaningful tab, dialog, drawer, error, and empty state in light and
   dark modes. Require zero critical/serious violations and triage every moderate issue. Automated
   checks must be supplemented by keyboard, zoom, and screen-reader scripts. [R1–R5, R7–R10]
2. Give every chart a nearby table, ranked list, or downloadable equivalent; never require color
   or pointer hover to recover a value or status. Use position/length encodings for precise
   comparison. [R2, R9, R24]
3. Announce long-running state changes with restrained live regions: queued, started, progress
   milestones, completed, failed, reconnected, and cancelled. Keep visual progress visible for
   waits over roughly one second. [R11, R16, R21]
4. Require a review/confirmation boundary for irreversible, high-impact, fleet-wide, or
   data-changing actions. State the object count, scope, engine, estimated work, consequence, and
   recovery path; do not add confirmations to routine reversible actions. [R6, R8, R17, R21]
5. Make meaningful tabs and filters URL-addressable so refresh, back/forward, bookmarks, support
   links, and shared incident links preserve context. [R11, R18, R21]
6. Standardize forms on persistent labels, field-level errors, a focused error summary, preserved
   input, and actionable copy. Prefer structured controls over raw JSON for common tasks. [R10,
   R12, R19]
7. Test all non-tabular content at a 320 CSS px equivalent and 400% zoom. Keep unavoidable
   two-dimensional table or diagram scrolling inside the component rather than on the page. [R3]
8. Add an authenticated not-found page. The current route tree has no wildcard route, so an
   unknown URL can produce an empty shell with Health highlighted; provide explanation, search,
   recent destinations, and recovery actions. [R11, R20]

## Page-by-page findings and path to 10/10

### 1. Login — 8.1/10 — High confidence

**Current strengths**

- Focused single-card flow, visible labels, username autocomplete, required fields, loading state,
  and an explicit SSO path.
- Authentication failures use an alert rather than disappearing into a toast.
- Mobile layout is inherently simple and the current screen has light/dark visual baselines.

**To reach 10/10**

1. Replace production-facing infrastructure copy such as environment-variable names with
   user-facing recovery guidance; put deployment diagnostics behind an admin detail or support
   code. Recognition is better than requiring users to interpret implementation details. [R11,
   R15]
2. When sign-in fails, focus or announce the error, preserve the username, explain the next action,
   and expose a retry/contact-support path. [R10, R11, R19]
3. Verify keyboard order, password-manager/SSO behavior, 400% zoom, contrast in both themes, and
   24-by-24 CSS px minimum targets; retain the current visible labels. [R1, R3–R5, R10]

### 2. Login Callback — 5.6/10 — Low confidence

**Current strengths**

- Minimal waiting screen, visible spinner, screen-reader-only busy text, and an alert role for the
  returned error.

**To reach 10/10**

1. On failure, stop presenting an indefinite “completing” state and show a recovery card with
   **Try again**, **Return to sign in**, and a copyable correlation/support code. Empty/error states
   should keep users on a productive path. [R11, R20, R21]
2. Treat missing authorization code, rejected code, timeout, and network failure as distinct
   outcomes with specific, non-technical guidance; do not silently route an invalid callback away.
   [R11, R16, R20]
3. Expose a programmatic busy state and announce success/failure once without repeatedly reading
   spinner changes. [R7, R11, R16]

### 3. Datalake Health — 7.9/10 — High confidence

**Current strengths**

- Clear executive health framing, first-run guidance, actionable alerts, scan freshness, grade
  breakdown, compact trend charts, and a good progression from status to table detail.
- Snapshot freshness uses mutually exclusive time buckets based on the latest committed Iceberg
  snapshot; repeated scan failures and missing timestamps remain explicit. Every bucket opens an
  exact, URL-backed Catalog list whose CSV export includes the complete server-filtered result.
- Empty and not-yet-scanned states explain the next step.

**To reach 10/10**

1. Remove or consolidate repeated compaction/savings numbers so each KPI answers one decision;
   reserve the strongest emphasis for health, risk, and required action. [R14, R15, R22]
2. Validate the accessible data-table equivalents and freshness drill-down comprehension with
   representative operators rather than treating implementation as usability evidence. [R2, R9, R24]
3. Measure whether operators correctly distinguish table freshness from Cryo scan freshness and
   recognize repeated scan failures as a separate condition. [R11, R13, R21]
4. Announce scan freshness transitions and new critical conditions without announcing every poll.
   [R11, R16, R21]

### 4. Catalogs — 8.3/10 — Medium confidence

**Current strengths**

- Strongest primary workflow: URL-backed filters, search, sorting, grouping, CSV export,
  select-page/select-all-matches, filter-spec bulk execution, and safe plan preview.
- Excellent support for finding, comparing, and acting on records, the four core data-table tasks
  identified by NN/G. [R13]

**To reach 10/10**

1. Add named/saved views, column visibility, reordering, and optional pinning for the 13-column
   table; default views should differ for health triage, cost, and maintenance. This reduces
   horizontal scanning without replacing comparison-friendly tables with cards. [R13, R15]
2. Decide and document the keyboard model: keep standard semantic-table navigation with tabbable
   controls, or implement the complete WAI-ARIA grid pattern. Do not create a partial hybrid.
   [R9, R13]
3. Give enabled/source/health indicators explicit accessible text rather than relying on a hidden
   decorative dot or color. [R2]
4. Verify the page at 320 CSS px equivalent; keep table scrolling inside the table container and
   keep filters, selection summary, and bulk actions available without page-level horizontal
   scrolling. [R3]
5. For large result sets, expose query progress and result-count changes in a polite live region,
   while avoiding announcements on each keystroke. [R11, R16]

### 5. Table Detail — 7.5/10 — Medium confidence

**Current strengths**

- Six coherent tabs, strong identity/KPI area, health and history context, direct operations,
  explainable Plan Preview, and precise SQL/payload disclosure.
- The redesign reduced the former nine-tab burden and preserves deep operator detail.

**To reach 10/10**

1. **P0:** Put **Repair silent data loss** behind a review step and alert dialog. Show the exact
   table, detected symptom/evidence, operation sequence, engine, expected data impact, estimated
   work, and recovery/rollback limitations before execution. [R6, R8, R17, R21]
2. Make all six tabs URL-addressable and preserve the selected tab across refresh, back/forward,
   copied incident links, and action completion. [R7, R11, R18]
3. Replace the bespoke “table not found” clickable spans with the shared page/empty-state pattern,
   actual links/buttons, a page-level heading, search, and a return-to-catalog action. [R5, R11,
   R20]
4. Add table equivalents and keyboard paths for charts/history; announce new operation status and
   refresh results. [R2, R9, R16, R24]
5. In Plan Preview, distinguish **estimate**, **verified plan**, and **actual executed result**;
   expose preview loading/errors and the assumptions behind cost and savings. [R11, R16, R25]

### 6. Cost — 8.0/10 — Medium confidence

**Current strengths**

- Clear separation of overview, S3 requests, and AWS integrations.
- Strong precision labels, learned-versus-heuristic transparency, waterfall/trend views,
  opportunity ranking, and direct action flow.

**To reach 10/10**

1. Add equivalent tables for waterfall, trends, request mix, and forecast views; use direct labels
   and non-color cues for current/projected/savings series. [R2, R9, R24]
2. Show model provenance, last-updated time, confidence/uncertainty range, and assumptions next to
   learned or heuristic forecasts so users calibrate reliance appropriately. [R11, R25]
3. Make active tab, period, source, and cost model URL-backed; preserve them when opening a table or
   returning from an action. [R11, R18, R21]
4. Reduce KPI repetition and make each opportunity state its calculation basis, expected range,
   and the next verifiable action. [R14, R15, R25]

### 7. Cost Attribution — 6.3/10 — Low confidence

**Current strengths**

- Useful team/department table, explicit attribution rate, unattributed cost, cost-component
  breakdown, and constructive setup guidance when tags are missing.

**To reach 10/10**

1. Replace the pie chart with a sorted horizontal bar or dot plot and retain the exact table.
   Position/length judgments support more accurate comparison than angles/areas, especially with
   many teams. [R24]
2. Make **View tables** deep-link to Catalogs with the selected cost center/department already
   applied instead of opening the generic explorer. [R11, R13, R21]
3. Add a persistent label to the month selector and encode the selected month in the URL. [R10,
   R11]
4. Render totals as a semantic table footer rather than a visually aligned seven-column grid that
   can drift from horizontally scrolled columns. [R3, R9, R13]
5. Decide whether attribution is a supported product surface: add it to Cost navigation and tests,
   or mark it explicitly experimental. A valuable direct route should not remain undiscoverable.
   [R11, R14]

### 8. Operations — 7.7/10 — High confidence

**Current strengths**

- Good fleet summary, clear Workflows/All Runs split, schedule and trigger context, recent history,
  quick links, and dense but readable run comparison.

**To reach 10/10**

1. Do not label a workflow with no historical run as **queued**; use **Not run**, **Ready**, or a
   similarly factual state. Status vocabulary must match the system state. [R11, R21]
2. Explain why **New preset** is disabled and what prerequisite/role unlocks it; do not leave a
   visible dead control without guidance. [R11, R20]
3. Keep the active Workflows/Runs tab, filters, sort, and pagination in the URL. [R7, R11, R18]
4. Add an accessible table/list equivalent for history bars and announce newly queued/finished
   runs without announcing every poll. [R2, R9, R16]

### 9. Scanner workflow — 7.7/10 — Medium confidence

**Current strengths**

- Consistent workflow shell, status KPIs, active-run link, recent history, fan-out pipeline,
  scoped trigger form, schedule controls, and confirmation before queueing.

**To reach 10/10**

1. Encode Overview/Grid/Graph/Runs/Config in the URL and preserve direct links; the current local
   tab state silently resets on refresh. [R7, R11, R18]
2. Make scan confirmation repeat the selected lakehouse, schemas/tables, estimated count, full-scan
   behavior, and conflict with an active run rather than only saying “selected scope.” [R6, R17]
3. Show a human-readable schedule, timezone, and next execution beside cron input; include a
   focused error summary linked to invalid fields. [R10, R15, R19]
4. Announce active-run start/completion and queue changes, and expose retry/recovery when status or
   config queries fail. [R11, R16, R20]

### 10. Preset workflow — 7.7/10 — Medium confidence

**Current strengths**

- Unified Overview/Grid/Graph/Runs/Config model, readable operation order, simulation-derived
  matched table/size context, last-run history, and prominent latest failure.

**To reach 10/10**

1. Encode the selected tab in the URL and preserve it after edits/runs. [R7, R11, R18]
2. Before saving or running, summarize changed scope, matched count/size, operation order,
   schedule/timezone, engine, and expected work; require confirmation only when impact is broad or
   destructive. [R6, R17, R21]
3. Separate simulation loading, stale, empty, and failure states so a missing count cannot be
   mistaken for a genuine zero. [R11, R16, R20]
4. Add field-linked error summaries and plain-language help for rules/cron while progressively
   disclosing advanced JSON or engine parameters. [R10, R12, R19]

### 11. Run Detail — 8.7/10 — Medium confidence

**Current strengths**

- Excellent breadcrumbs, live polling fallback, pipeline, progress, jobs, logs, history,
  cancellation/force confirmations, and failure drill-down.
- Status, work performed, and supporting evidence are visible in the same operational context.

**To reach 10/10**

1. Announce meaningful progress milestones, reconnect/fallback state, completion, cancellation, and
   failure through a polite live region; avoid reading every log line or percentage update. [R11,
   R16, R21]
2. Make log search/filter/copy/download controls fully keyboard operable and expose an accessible
   count of hidden/matching entries. [R4, R5, R9]
3. Show elapsed and estimated remaining time with the estimate’s basis/uncertainty; label stalled
   work distinctly from slow work. [R11, R16, R25]
4. Verify narrow/zoom layouts keep the primary status and safe actions visible while containing
   logs and job tables. [R3]

### 12. Job Detail — 8.8/10 — Medium confidence

**Current strengths**

- Best diagnosis surface: confidence, evidence, failure class, operator feedback, resource
  prediction, admission context, generated SQL, progressive raw-data disclosure, and confirmed
  force run.

**To reach 10/10**

1. Add explicit provenance and timestamps to diagnosis/confidence/resource predictions, including
   whether each conclusion is deterministic, heuristic, or model-generated. [R11, R25]
2. Give SQL and evidence blocks copy/download controls, keyboard focus, wrap/scroll choices, and
   accessible labels. [R3–R5, R9]
3. Explain how operator feedback changes future diagnosis and expose a reversible correction or
   audit trail. [R11, R21, R25]
4. Verify that force-admission confirmation always includes current load, bypassed guardrail,
   affected engine/table, and likely consequence. [R6, R17, R21]

### 13. Smart Maintenance — 7.7/10 — Medium confidence

**Current strengths**

- Clear approval-versus-execution separation, recommendations, action confirmations, and useful
  operational context.

**To reach 10/10**

1. Present recommendation evidence, confidence, rule/model source, last evaluated time, and
   expected benefit/risk before approval. Calibrated trust is preferable to generic “smart”
   authority. [R11, R25]
2. Make **Approve**, **Execute**, and **Dismiss** visually and semantically distinct, and state what
   each changes, whether it can be reversed, and who approved it. [R6, R11, R21]
3. For bulk approval/execution, show exact matched count, filter scope, engine, budget, and
   operation sequence in the review step. [R6, R17]
4. Make empty/no-recommendation states explain whether the system is healthy, still learning,
   stale, unconfigured, or filtered to zero. [R20, R25]

### 14. Intelligence — 7.6/10 — Medium confidence

**Current strengths**

- Evidence-aligned progressive disclosure into verified answers, AI/chat/MCP, and advanced
  telemetry; deterministic fallback and “still learning” language avoid pretending all output is
  equally certain.

**To reach 10/10**

1. Attach sources, timestamps, model/provider, confidence, and deterministic-versus-generated
   labels to every answer/recommendation; keep evidence near the claim. [R11, R25]
2. Replace common raw JSON/MCP input with labelled structured controls, examples, validation, and
   a focused error summary; retain JSON only as an advanced mode. [R10, R12, R19]
3. For streamed responses, expose generating/completed/failed state, a stop action, and restrained
   screen-reader announcements. [R11, R16, R21]
4. Test prompt/action choices with operators and keep the first tier task-oriented; progressive
   disclosure should reduce complexity, not merely hide unexplained controls. [R12, R15, R22]

### 15. Governance — 6.8/10 — Medium confidence

**Current strengths**

- Rich coverage of attention, inventory, intelligence, policies, approvals, anomalies,
  observations, lineage, ABAC, GitOps, and FinOps with generally transparent state.

**To reach 10/10**

1. Reorganize around operator jobs and roles—**Triage**, **Ownership & inventory**, **Access &
   policy**, and **Change/audit**—then progressively disclose specialist tools. The current breadth
   creates avoidable choice and cognitive load. [R12, R15, R22]
2. Give each major task a stable URL and mount/fetch only the active area instead of force-mounting
   all tabs. Preserve filters when moving between related tasks. [R7, R11, R18]
3. Replace 200-item table pickers with server search, selected-item persistence, and explicit
   result counts so large estates are not silently incomplete. [R11, R13]
4. Standardize policy/approval forms on labels, field errors, error summaries, review steps, and
   audit consequences. [R6, R10, R19]
5. Provide keyboard/text alternatives for lineage or relationship visualizations and verify
   reflow/contained scrolling. [R2–R5, R9, R24]

### 16. Query Performance — 7.8/10 — Medium confidence

**Current strengths**

- Strong telemetry-required state, useful workload metrics, query table, and icon-plus-text status.
- Query data is oriented toward diagnosis rather than decorative dashboarding.

**To reach 10/10**

1. Add persistent labels for engine/time selectors and encode them in the URL. [R10, R11]
2. Open full query text, execution stages, affected tables, and actions in an accessible detail
   drawer/page rather than depending on truncation/hover. [R12, R13]
3. Add table equivalents for distributions/trends and use position/length rather than color alone
   for precise comparison. [R2, R9, R24]
4. Make telemetry setup errors identify the missing connection/configuration and provide a direct
   corrective action. [R11, R20]

### 17. SLA Monitoring — 7.8/10 — Medium confidence

**Current strengths**

- Strong operational triage, freshness/availability framing, violation lists, and direct paths to
  the tables that need attention.

**To reach 10/10**

1. **P0:** Replace or supplement clickable Recharts bars with keyboard-focusable filters and a
   semantic data table. Pointer-only chart selection does not provide an equivalent interaction.
   [R4, R5, R9]
2. Label thresholds, time window, timezone, freshness source, and stale-data state directly beside
   the chart/table. [R11, R14, R24]
3. Announce new/cleared violations and filter-result counts without announcing every refresh.
   [R11, R16, R21]
4. Make the selected status/bucket URL-backed so incidents can be shared and revisited. [R11, R18]

### 18. Search Results — 6.1/10 — Medium confidence

**Current strengths**

- Useful grouped results for catalogs, schemas, tables, and columns; semantic tables; contextual
  match explanations; and links to destination records.

**To reach 10/10**

1. Adopt the shared `PageHeader` and async-state patterns so loading, retryable error, empty query,
   and no-results states have consistent hierarchy and actions. [R11, R20]
2. Add a visible search field plus group chips/tabs with counts; the URL-supported `group` filter
   should not be reachable only through a “refine” link. [R11, R15, R22]
3. Announce “searching,” total results, selected group, and no-results status in a polite live
   region; debounce to avoid announcing every keystroke. [R11, R16]
4. Replace deployment-internal error copy with **Retry**, connection/support guidance, and optional
   technical details. [R11, R20]
5. Provide pagination/load-more for totals above 50 and keep native result tables in contained
   horizontal scrollers at narrow widths. [R3, R13]

### 19. Connections — 7.9/10 — Medium confidence

**Current strengths**

- Excellent separation of read-side Lakehouses and write-side Engines, clear recommended setup
  sequence, health KPIs, testing, encrypted-secret messaging, confirmations for deletion, and
  dedicated AI/Slack/Ops integration areas.

**To reach 10/10**

1. Encode Lakehouses/Engines/AI/Slack/Ops and the edited connection in the URL so setup links,
   refresh, and support handoffs retain context. [R7, R11, R18]
2. Use progressive disclosure in complex drawers: identity and required connectivity first,
   authentication second, tuning/advanced options last, with a review/test step before save. [R12,
   R15, R22]
3. Add a focused form error summary linked to persistent field labels; preserve entered secrets
   appropriately and never echo stored secret values. [R10, R19]
4. Replace raw JSON capacity-test output with a human summary, categorized warnings, next actions,
   and an optional technical-details disclosure. [R11, R12, R20]
5. Verify every secret is redacted in tables, alerts, test results, network-error copy, telemetry,
   and screenshots; explain replacement semantics before users overwrite a credential. [R6, R11]

### 20. AI Skillsets — 6.5/10 — Medium confidence

**Current strengths**

- The formerly skeletal surface now has lifecycle metrics, creation, versioning, promotion,
  activation, and rollback workflows.

**To reach 10/10**

1. Require review/confirmation for **Activate** and **Rollback**, including current/new version,
   affected consumers, compatibility/validation result, audit actor, and recovery path. [R6, R8,
   R17, R21]
2. Replace raw JSON editors for common schemas with structured, labelled, schema-aware fields,
   autocomplete, inline validation, and a focused error summary; keep source mode for experts.
   [R10, R12, R19]
3. Expose lifecycle as explicit text—draft, validated, active, superseded, rolled back, failed—not
   color alone; show immutable version provenance and promotion history. [R2, R11, R25]
4. Add a safe preview/test harness with representative input, deterministic validation output, and
   clear separation between saving a draft and changing production behavior. [R6, R11, R21]

## Prioritized roadmap

### P0 — Safety and equal access

| Improvement | Why now | Research basis | Done when |
|---|---|---|---|
| Guard Table Detail repair and Skillset activate/rollback | Current high-impact actions are not consistently reviewable | [R6, R8, R17, R21] | Exact scope/stakes/recovery are shown and confirmed; audit event exists |
| Make SLA/chart interactions keyboard and text accessible | Pointer/color cannot be the only way to operate or read data | [R2, R4, R5, R9, R24] | Every chart has equivalent values/actions; full keyboard path passes |
| Make accessibility CI deterministic and enable contrast | Current suite can skip and explicitly disables contrast | [R1–R5] | All surfaces/states run in CI, both themes, with no disabled WCAG rules |
| Standardize form labels and error summaries | Connection/Skillset/Governance forms are high-stakes and complex | [R10, R19] | Focus moves to summary; links target every invalid field; values persist |
| Add Login Callback recovery | Authentication failure currently has no productive exit | [R11, R16, R20] | Retry/sign-in/support paths are keyboard and screen-reader verified |

### P1 — Findability, comprehension, and trust

| Improvement | Why now | Research basis | Done when |
|---|---|---|---|
| URL-back all meaningful tabs and filters | Refresh/shared incident links currently lose context | [R7, R11, R18] | Back/forward, reload, bookmark, and copied URLs preserve view |
| Rebuild Search around shared states and visible filters | Search is below the product’s otherwise consistent standard | [R11, R13, R20, R22] | Visible group control, retry, pagination, live result status |
| Reframe Governance around role/task | Too many domains compete at one hierarchy level | [R12, R15, R22] | Operator tests find the correct task without tab guessing |
| Replace attribution pie and repair its deep links | Current encoding and generic link weaken the decision path | [R13, R24] | Sorted bar/dot + table; team link opens prefiltered tables |
| Expose AI/model provenance and uncertainty consistently | Appropriate reliance requires calibrated trust | [R11, R25] | Every automated conclusion shows source/type/time/confidence |
| Add table column presets/personalization | Core 13-column comparison is powerful but demanding | [R13, R15] | Role defaults plus saved views; no loss of bulk-selection behavior |

### P2 — Refinement and measurable optimization

| Improvement | Why | Research basis | Done when |
|---|---|---|---|
| Add authenticated not-found recovery | Invalid links currently yield an empty shell | [R11, R20] | Search, home, back, and useful destinations are available |
| Tune dashboards to one KPI per decision | Some metrics repeat with competing emphasis | [R14, R15] | Comprehension test identifies status/risk/action correctly |
| Refine target size and touch layouts | Dense desktop controls can be small on touch/zoom | [R3, R5, R23] | Target and reflow checks pass at defined viewports/zoom |
| Expand visual regression | Only Login, Health, and Operations have baselines | [R11, R21] | Critical empty/error/dialog/mobile states are baseline-protected |

## Additional ways to evaluate and improve Cryo

The two scorecards above are expert assessments. Expert review is valuable, but usability is an
**outcome of use in a context**, not a property that can be proven by looking at screens. ISO
9241-11 frames usability around use; established HCI guidance separates learnability, efficiency,
memorability, errors, satisfaction, and utility. [R32, R33]

Cryo should therefore maintain two independent records:

1. **Quality score:** what the current implementation appears to support (the scorecards above).
2. **Evidence maturity:** how strongly each claim has been demonstrated with automation, users,
   repeated tasks, production behavior, and field context.

A visually strong page with no user evidence can be promising, but it is not yet 10/10.

### Evidence-maturity scale

| Level | Evidence | What it proves | Maximum defensible score |
|---|---|---|---:|
| E0 | Opinion or mockup only | A proposed direction exists | 5/10 |
| E1 | Structured expert review and source inspection | Known principles are followed | 7/10 |
| E2 | Automated functional, accessibility, visual, and performance checks | Repeatable implementation properties | 8/10 |
| E3 | Formative testing with representative users | Major behavioral problems have been observed and iterated | 8.5/10 |
| E4 | Quantitative benchmark with confidence intervals | Effectiveness, efficiency, and satisfaction meet targets | 9/10 |
| E5 | Field/longitudinal evidence across roles, scale, and assistive technology | The experience works over time in realistic conditions | 10/10 |

The maximums are Cryo governance rules, not claims that a research paper defines universal score
caps. Their purpose is to prevent subjective polish from being mistaken for demonstrated quality.
Qualitative studies identify *why* users struggle; quantitative studies show *how much* and whether
the product improved. Both are required. [R39, R48]

### Expanded evaluation dimensions

| Dimension | Core question | Best evaluation | Primary metrics | 10/10 Cryo gate |
|---|---|---|---|---|
| Utility and task coverage | Does Cryo support the work operators actually need? | Field study, contextual inquiry, top-task interviews | Critical tasks covered; workaround/export count; external tools used | Every critical job has an effective in-product path; no safety-critical spreadsheet/CLI workaround is required [R33, R45] |
| First-use learnability | Can a qualified new operator succeed without training? | Cognitive walkthrough followed by moderated novice testing | First-attempt success; assistance; wrong first action; time to first value | ≥90% unassisted success on onboarding and first maintenance preview; no critical error [R34, R35] |
| Learning curve | How quickly do repeated users become proficient? | Repeated-trial quantitative study | Time/error curve over trials; trials to plateau | Core task time falls materially across the first three trials and reaches the predefined expert target [R35] |
| Expert efficiency | Can frequent operators work quickly without losing safety? | Quantitative benchmark with experienced users | Median/p90 time; steps; backtracks; batch accuracy; accelerator use | ≥95% success; 20% faster than baseline without higher error/scope-correction rate [R33, R48] |
| Memorability | Can occasional users return and recover proficiency? | Delayed retest after 2–4 weeks | Retained success; time/error regression; help use | ≥90% success and ≤15% task-time regression versus the participant's learned trial |
| Cognitive workload | Does the UI demand avoidable mental effort? | NASA-TLX after critical tasks plus observation | Overall and six subscale ratings; hesitation; context switching | No universal NASA-TLX cutoff is asserted; redesigned flow must improve workload with confidence intervals and no performance regression [R38] |
| Mental-model fit | Does Cryo behave as users predict? | Interview, concept mapping, cognitive walkthrough, teach-back | Prediction accuracy; terminology mismatch; conceptual errors | ≥90% correctly explain Source vs Engine, scan vs maintenance, preview vs execute, and recommendation confidence [R34, R54] |
| Information architecture | Can each role find the right feature? | Open/closed card sort, tree test, first-click test | Direct success; path; time; category agreement; first click | ≥85% direct success for critical destinations; ≤5% failure; role terminology is understood [R36, R37, R50] |
| Content comprehension | Are labels, errors, units, and instructions understood? | Cloze/teach-back, error-recovery tasks, terminology test | Correct paraphrase; recovery success; jargon failures | ≥90% correctly explain status, consequence, unit/timeframe, and next action; no blame or deployment-internal copy [R43, R52] |
| Error tolerance and recovery | Can users prevent, detect, undo, and recover? | Fault injection and destructive-action simulations | Critical error rate; detection; recovery time; lost input; undo success | Zero accidental destructive execution; 100% scope recognition; recoverable failures preserve safe input and context [R6, R11, R52] |
| Interruption resilience | Can an operator resume after an incident or handoff? | Interrupted-task study and paired-role handoff | Resume time; state loss; duplicate action; handoff completeness | Refresh/back/reconnect preserve state; another operator can resume from a permalink and audit trail without oral reconstruction [R11, R21, R45] |
| Accessibility in use | Can people with disabilities complete the same work? | Standards audit plus testing with disabled users and assistive technologies | Task completion; blockers; extra time; announcement/focus errors | All critical tasks complete with keyboard, screen reader, zoom/reflow, switch/voice where applicable; no workaround-only path [R1–R10, R40, R44] |
| Perceived usability and satisfaction | Do users find Cryo useful and easy after using it? | SUS or UMUX-Lite post-test; SEQ after each task | SUS/UMUX-Lite; SEQ; confidence intervals; qualitative reason | SUS ≥85 or validated equivalent, no role below 80; task ease improves without masking poorer performance [R26, R39, R40] |
| Performance and responsiveness | Does the UI feel immediate under real load? | Lab budgets plus real-user monitoring | LCP, INP, CLS p75; API-to-useful-state; long-task count | At p75 on supported devices: LCP ≤2.5s, INP ≤200ms, CLS ≤0.1; immediate acknowledgement for long operations [R16, R42] |
| Visualization comprehension | Can users read the chart and make the right decision? | Timed chart-reading tasks with keyboard and non-chart alternative | Value/trend/threshold/action accuracy; time; confidence | ≥90% correct for value, comparison, trend, threshold, and action; table alternative has equivalent information [R2, R9, R24, R31] |
| Trust calibration | Do users rely on automation only when appropriate? | Controlled correct/incorrect recommendation study | Appropriate accept/reject; over-reliance; under-reliance; confidence calibration | ≥90% appropriate reliance; ≤5% harmful over-reliance; evidence/provenance changes decisions in the intended direction [R25] |
| Security and permission comprehension | Do users understand access, secrets, and denied actions? | Role-based scenario testing and secret/error review | Permission prediction; escalation success; secret exposure; unsafe retries | 100% understand why an action is unavailable and how to request access; no secret appears in UI, logs, telemetry, or screenshots [R6, R11, R52] |
| Responsiveness and international resilience | Does layout survive device, zoom, long text, locale, and identifiers? | Viewport/zoom matrix, pseudo-localization, Unicode/RTL/long-ID fixtures | Overflow; truncation; clipping; lost meaning; locale errors | No non-exempt page overflow at 320 CSS px; safe grapheme-aware truncation; dates/numbers/timezones remain explicit [R3, R53] |
| Longitudinal value and adoption | Does Cryo improve work over weeks, not just one session? | HEART metrics, diary study, field analytics | Adoption; task success; retention; feature abandonment; support burden | Critical workflows remain used and successful; no rise in support incidents, shadow tools, or recommendation abandonment [R41, R46, R55] |
| Emotional quality and brand trust | Does Cryo feel calm, capable, and credible under pressure? | Desirability/word-choice study after realistic incidents | Selected adjectives; confidence; stress; perceived professionalism | ≥80% choose target attributes (clear, calm, trustworthy, capable); ≤10% choose confusing, risky, or overwhelming [R26–R30] |
| Design-system consistency and UX debt | Does new work preserve learned behavior? | Component inventory, interaction diff, UX-debt register | Primitive reuse; divergent affordances; overdue UX debt | No undocumented interaction variant; every accepted UX debt item has owner, impact, due milestone, and regression test [R11, R51] |

### Cryo top-task benchmark

Use the same task set for before/after comparisons. Do not tell participants which page, tab, or
button to use; realistic tasks must express goals without embedding interface labels. [R47]

| ID | Role | Scenario | Primary evidence |
|---|---|---|---|
| T1 | New operator | Connect a lakehouse, verify it, and discover its first tables | First-use success, time to first value, error recovery |
| T2 | Operator | Find the tables creating the most immediate health risk and explain why | First click, information scent, comprehension |
| T3 | Operator | Select every table matching a risk condition and preview maintenance without running it | Filter/scope accuracy, batch confidence, plan comprehension |
| T4 | Operator | Decide whether a proposed plan is safe for Trino right now | Guardrail comprehension, trust calibration, workload |
| T5 | Operator | Start approved work, follow progress, and cancel it safely when conditions change | Feedback, interruption handling, cancellation safety |
| T6 | Incident responder | Diagnose a failed job and choose the least risky next action | Diagnosis accuracy, evidence use, recovery time |
| T7 | Data engineer | Explain why one partition is recommended for optimization and verify the rendered SQL/payload | Data/visualization comprehension, provenance |
| T8 | FinOps | Identify the largest credible savings opportunity and explain estimate uncertainty | Chart accuracy, model trust, deep-link continuity |
| T9 | Governance owner | Find an ownership/policy gap, identify affected tables, and create the corrective action | IA, broad-workflow comprehension, form recovery |
| T10 | Platform admin | Add/test an engine using the correct authentication method without exposing a secret | Form learnability, security comprehension |
| T11 | Viewer using keyboard/screen reader | Find an SLA breach and open the affected table | Accessibility task parity, chart alternative |
| T12 | Returning occasional user | Reopen a shared failed-run link after two weeks and continue the investigation | Memorability, permalink context, handoff quality |
| T13 | On-call pair | Hand an in-progress incident from one operator to another | Audit trail completeness, resume time, duplicate-action prevention |
| T14 | AI-assisted operator | Review one correct and one intentionally flawed recommendation | Appropriate reliance, evidence sensitivity, over-reliance |

Use production-shaped seeded data: multiple lakehouses and engines, at least 100,000 tables for
scale tests, long catalog/schema/table names, mixed health grades, stale and missing scans, active
and failed runs, role restrictions, degraded engine probes, AI unavailable/low-confidence states,
and timezone boundaries. Toy zero-data screens cannot validate operational comprehension.

### Exact study instructions

#### 1. Weekly cognitive walkthrough

Run a 60–90 minute cross-functional workshop for one critical flow with a facilitator, recorder,
operator/domain expert, designer, engineer, and accessibility reviewer. At every step ask:

1. Will the user try to achieve the right result?
2. Will the user notice the correct action?
3. Will the user associate that action with the intended result?
4. After acting, will the user see progress toward the goal? [R34]

Record **pass/fail**, evidence, likely user misconception, severity, and proposed experiment. A
single “no” fails the step. Do not use the workshop as a substitute for user testing.

#### 2. Formative moderated testing

- Run small iterative rounds with 3–5 comparable participants, then redesign and repeat; do not
  spend the whole budget observing the same known failure. Distinct roles require separate coverage.
  [R33, R56]
- Recruit outside the implementation team. Include novice and experienced operators, viewers,
  admins, governance/FinOps users, and people with disabilities.
- Use 5–7 realistic tasks per 60-minute session. Ask participants to work naturally; do not coach,
  point, explain labels, or ask “would you click this?” [R47]
- Capture screen/audio with consent, first click, completion, time, errors, backtracks, help,
  confidence, and one SEQ-style ease rating immediately after each task. Ask “why?” after the rating,
  not during the task. [R39]
- Classify issues: **critical** (unsafe/data-impacting or task impossible), **high** (task failure),
  **medium** (delay/confusion/workaround), **low** (friction/polish).

#### 3. Quantitative benchmark

- Use at least 20 usable participants per comparison cohort as a practical quantitative baseline;
  plan a larger sample when detecting smaller effects or segmenting by role/expertise. Calculate
  confidence intervals instead of reporting naked averages. [R39, R48]
- Freeze tasks, data, environment, instructions, and scoring rules before testing.
- Randomize task order where learning will not invalidate the scenario. For design/competitor
  comparisons, counterbalance product order. [R49]
- Report unassisted binary success, partial success, critical errors, median and p90 task time,
  error/backtrack count, optimal-path deviation, SEQ per task, and SUS/UMUX-Lite post-test.
- Never claim improvement from satisfaction alone. Subjective questionnaires need behavioral
  success/time/error data beside them. [R39]

#### 4. Learnability and memorability study

- Recruit 30–40 people with little or no Cryo experience; stratify prior experience with comparable
  tools. [R35]
- Repeat T1, T3, T6, and T10 across at least three trials and plot each participant's time and
  errors rather than only a group average.
- Retest T6 and T12 after 2–4 weeks without refresher training.
- Preserve both novice guidance and expert accelerators. A wizard that never becomes faster is
  learnable but inefficient; shortcuts that novices cannot discover are efficient but unlearnable.
  [R35]

#### 5. Information-architecture study

1. Run an open card sort using 30–50 cards representing Cryo tasks/objects, not current page names.
   Use at least 15 participants for qualitative patterns or 30–50 for quantitative grouping. [R36]
2. Propose role-oriented navigation from the result.
3. Run a tree test on both current and proposed structures using T2, T6, T8, T9, and T10. Measure
   direct success, indirect success, failure, first choice, path, and time. [R37]
4. Interview failed participants about label meaning. Improve information scent—the label,
   surrounding description, and prior knowledge that help users predict destination value. [R50]

#### 6. Accessibility-with-users study

- Do not stop at axe or WCAG checklists. Include people who use screen readers, keyboard only,
  zoom/magnification, voice control, reduced motion, and cognitive adaptations as relevant.
  Standards conformance and user evaluation answer different questions. [R40, R44]
- Test the same T1–T14 goals, not simplified “accessibility tasks.”
- Record extra steps/time, inaccessible alternatives, focus/announcement failures, orientation
  loss, fatigue, and whether assistance was required.
- Fix blockers before cosmetic accessibility issues, then retest the affected flow with the same
  access strategy.

#### 7. Cognitive-workload and interruption study

- After T3, T4, T6, T9, and T13, administer NASA-TLX dimensions: mental, physical, temporal demand,
  performance, effort, and frustration. Compare redesigns within the same task; do not invent a
  universal “good” absolute cutoff. [R38]
- Interrupt participants midway with a realistic alert, five-minute secondary task, browser
  refresh, connection drop, or operator handoff.
- Measure resume time, lost selections/filters/input, duplicate action, incorrect assumption, and
  whether Cryo clearly shows what already happened.

#### 8. Trust-calibration study

- Prepare matched recommendations: correct/high-confidence, correct/low-confidence,
  incorrect/high-confidence-looking, stale, contradictory, and deterministic fallback.
- Ask participants to approve, reject, investigate, or defer while explaining evidence used.
- Vary provenance/confidence presentation without changing the recommendation to learn whether the
  cues calibrate reliance rather than merely decorate the answer.
- A safer interface should increase acceptance of good automation and rejection of bad automation;
  reducing all automation use is not automatically a success. [R25]

#### 9. Field and longitudinal study

- Observe operators in their real environment to discover interruptions, Slack/terminal/spreadsheet
  workarounds, collaboration, scale, vocabulary, and tasks the product team did not know to ask
  about. [R45]
- Follow with a 2–4 week event-based diary study: log incidents, unexpected recommendations,
  handoffs, failed searches, abandoned flows, and work moved outside Cryo. Diary studies reveal
  habits and changing perceptions that one session cannot. [R46]
- Conduct a final interview using diary entries and product telemetry as prompts.

#### 10. Performance and scale evaluation

- Add lab budgets for JavaScript, route transition, data-table render, chart render, and command
  response, but judge production quality with real-user monitoring.
- Track Core Web Vitals at p75 separately for desktop/mobile and route/data-size cohorts: LCP
  ≤2.5s, INP ≤200ms, CLS ≤0.1. [R42]
- Test realistic worst cases: 100k+ tables, 500-column schemas, 10k log lines, hundreds of runs,
  slow 3G/VPN latency, packet loss, WebSocket reconnect, API 429/500/timeout, and partial backend
  availability.
- Measure time to **useful and trustworthy state**, not merely first paint. Skeletons do not make
  stale or incomplete data useful.

### UX scorecard for every release

Publish this table with baseline, candidate, delta, confidence interval, sample, and evidence link.
Do not collapse it into one vanity number.

| Measure | Release gate |
|---|---:|
| Critical-task unassisted success | ≥95% |
| General-task unassisted success | ≥90% |
| Accidental destructive or wrong-scope execution | 0 |
| Correct scope comprehension before execution | 100% |
| Median task-time regression | None >10% without documented safety benefit |
| Correct first action on top tasks | ≥90% |
| Direct tree-test success for critical destinations | ≥85% |
| Error recovery without lost safe input/context | ≥95% |
| Chart decision accuracy | ≥90% |
| Appropriate reliance on automation | ≥90% |
| Harmful over-reliance | ≤5% |
| Accessibility completion on critical tasks | 100% |
| SUS | ≥85 overall; no role cohort <80 |
| UMUX-Lite alternative | Report both items and validated SUS-equivalent with confidence interval [R40] |
| Core Web Vitals p75 | LCP ≤2.5s; INP ≤200ms; CLS ≤0.1 [R42] |
| Critical UX debt past due | 0 |

Targets are Cryo product goals, not universal research norms. If a target is missed, the release
decision must state severity, affected role/task, compensating control, owner, and due milestone.
Safety and accessibility gates cannot be waived by a high average satisfaction score.

### HEART measurement plan for production

Use HEART—Happiness, Engagement, Adoption, Retention, and Task success—but map every metric to an
explicit goal and user signal. Analytics should indicate and triangulate problems, not replace
observational research or become a collection of interesting but unactionable numbers. [R41, R55]

| HEART category | Cryo goal | Signals and measures |
|---|---|---|
| Happiness | Operators feel calm, capable, and in control | UMUX-Lite/SUS, task SEQ, trust, workload, target brand adjectives |
| Engagement | Users inspect evidence and follow safe workflows | Plan-preview depth, evidence expansion, saved views, permalink/handoff use—not raw clicks |
| Adoption | Teams complete first value and adopt core workflows | Time from connection to first scan, first safe preview, first successful maintenance |
| Retention | Teams continue using Cryo instead of shadow tools | Weekly role-based return, repeated successful workflows, diary-reported spreadsheet/CLI displacement |
| Task success | Users achieve correct outcomes safely and efficiently | Completion, time, error, scope correction, cancellation/recovery, appropriate AI reliance |

Add privacy-preserving event schemas for **intent, scope, outcome, duration, error class, recovery,
role, and experience cohort**. Never capture SQL, secrets, free-text prompts, table identifiers, or
raw payloads unless explicitly necessary, minimized, protected, and documented.

### Evaluation cadence and ownership

| Cadence | Activity | Accountable owner | Required output |
|---|---|---|---|
| Every UI pull request | Targeted component/state tests, axe with contrast, keyboard path, visual diff, responsive check | Feature engineer | Passing checks plus before/after evidence for changed states |
| Weekly | One critical-flow cognitive walkthrough and UX-debt triage | Product designer | Step log, severity, owner, hypothesis, and linked follow-up |
| Before implementation of a major workflow | Field/top-task discovery or evidence review; task and metric definition | Product manager + researcher | Problem statement, representative roles, top task, baseline, target |
| Per feature iteration | 3–5 comparable representative users; redesign before the next round | UX researcher | Observation clips/notes, issue severity, changes, retest result |
| Monthly | HEART/RUM/support review segmented by role, route, scale, and experience | Product analytics owner | Metric movement, hypotheses, and selected research questions |
| Quarterly | Fixed quantitative top-task benchmark and workload/trust checks | UX researcher | Baseline/candidate deltas, confidence intervals, release-gate status |
| Semiannually | Tree test, terminology test, and disabled-user accessibility study | Research + accessibility lead | IA/access barriers, prioritized remediation, verified retest |
| Annually | Field/diary and competitive same-task benchmark | Product leadership | Strategy findings, roadmap decisions, updated top-task set |

Engineering owns implementation quality, but should not facilitate its own summative study.
Product owns prioritization, research owns method validity, domain operators validate scenario
realism, accessibility specialists validate access strategies, and security/privacy owners approve
telemetry. Each critical finding has one named owner and a retest date.

### Additional enhancements this evaluation should drive

1. **Role-based first-run journeys:** guide viewer, operator, FinOps, governance, and admin users to
   their first meaningful outcome instead of presenting the same empty dashboard. Validate with T1,
   tree testing, and time-to-first-value. [R20, R35–R37]
2. **Novice/expert dual paths:** keep safe explanations and previews for new users; add saved views,
   keyboard shortcuts, command-palette actions, reusable filters, and bulk accelerators for experts.
   Compare first-use and plateau efficiency. [R21, R35]
3. **Interruption-safe workspace:** persist tab/filter/selection/draft state; show last confirmed
   action, stale-data status, reconnect state, and a resumable incident permalink. Validate with T5,
   T12, and T13. [R11, R21, R45]
4. **Explain the product model:** add concise Source vs Engine, scan vs maintenance, preset vs run,
   preview vs execute, and measured vs estimated explanations at the moment they matter. Test with
   prediction and teach-back rather than asking if copy “looks clear.” [R43, R54]
5. **Permission transparency:** replace unexplained hidden/disabled actions with role, reason,
   impact, and safe escalation path; keep unavailable destructive controls from appearing
   actionable. [R11, R43, R52]
6. **Blame-free recovery:** errors should name the failed object/action, preserved state, likely
   cause, safe next step, retry consequences, and support code; technical details remain
   progressive disclosure. [R19, R20, R52]
7. **Scale-resilient controls:** replace fixed 200/500-item client lists with server search,
   virtualization, selected-item persistence, explicit result completeness, and cancellable
   loading. Benchmark against production-sized estates. [R13, R16, R42]
8. **Collaborative incident handoff:** add shareable stateful URLs, actor/time audit timeline,
   notes/evidence bookmarks, and explicit ownership/next action so incident context survives shift
   changes. Validate through paired handoff, not single-user testing. [R21, R45, R46]
9. **Timezone and freshness clarity:** display timezone on schedules/deadlines, source timestamp,
   scan timestamp, age, and stale threshold; test DST and IST/UTC boundary cases. [R11, R43]
10. **International and long-data hardening:** pseudo-localize to 30–50% text expansion, test RTL
    layout where support is intended, long Unicode identifiers, grapheme-safe truncation, locale
    number/date formats, and copy/download access to untruncated values. [R3, R53]
11. **Recommendation disagreement workflow:** let operators reject, defer, correct, or request more
    evidence; preserve rationale and whether feedback changes future behavior. Validate calibration,
    not just approval rate. [R25]
12. **Managed UX-debt register:** track every deliberate inconsistency or deferred research finding
    with evidence, affected task/role, severity, owner, due milestone, and verification test. Review
    it beside technical debt each planning cycle. [R51]
13. **Comparative task benchmark:** compare Cryo with 2–3 relevant tools or incumbent workflows on
    the same randomized tasks, not screenshot aesthetics. Learn from stronger patterns without
    copying mismatched product models. [R49]
14. **Evidence-linked design decisions:** every substantial UI change records hypothesis, target
    task/metric, source evidence, release result, and rollback decision. This prevents repeated
    opinion debates and makes failed experiments useful. [R41, R48, R55]

## Validation plan for a genuine 10/10

### Automated release gates

1. **Route/state coverage:** every canonical surface in this audit, each meaningful tab, and every
   shared dialog/drawer has fixture-driven tests for populated, loading, empty, error, and
   permission-denied states. [R11, R20]
2. **Accessibility:** axe in light/dark with contrast enabled; zero critical/serious violations;
   all moderate/minor findings reviewed; no test may skip because a separately started API is
   absent. [R1–R5]
3. **Keyboard:** a scripted path completes search, filter, select-all-matches, preview, run,
   cancel, connection test, SLA filtering, and form correction without a pointer. [R4, R5, R7–R9]
4. **Reflow:** at 1280×1024 with 400% zoom and a 320 CSS px equivalent, no non-exempt region causes
   page-level horizontal scrolling or loses content/action. [R3]
5. **Safety:** every irreversible/high-impact operation has an automated assertion for review,
   scope, consequence, cancel, confirm, success/failure, and audit trail. [R6, R8, R17]
6. **Charts:** every chart has an equivalent labelled table/list and no action depends on hover,
   color, or pointer-only events. [R2, R9, R24]

### Initial moderated operator research

Run iterative cohorts of 3–5 comparable participants per materially different role, redesigning
between rounds. Cover platform operator, data engineer, FinOps, governance, viewer, and admin roles
over the research program; do not treat one participant from each role as one homogeneous sample.
Use representative estates and incidents rather than toy data. [R33, R56]

| Task | Target |
|---|---:|
| Find the highest-risk table and explain why | ≥90% success; median ≤60 seconds |
| Select all tables matching a filter and preview safe maintenance | 100% correct scope; 0 accidental runs |
| Diagnose a failed run/job and choose the next action | ≥90% correct diagnosis/action |
| Configure/test a lakehouse and engine | ≥90% success without assistance |
| Identify an SLA breach using keyboard only | 100% success |
| Explain confidence/provenance of an AI recommendation | ≥90% correct understanding |
| Recover from invalid search, auth callback, and form errors | ≥90% success |
| SUS | ≥85 after critical workflows |

Record completion, time, errors, backtracks, assistance, confidence, and comprehension. A page does
not earn 10/10 if it merely looks complete; it must meet its task targets without creating safety
or accessibility regressions.

### Production measures

- Preview-to-run conversion and cancellation rate by operation.
- Scope correction rate between filter, preview, and execution.
- Failed form submissions by field and successful recovery rate.
- Search no-result rate, refinement rate, and destination click-through.
- Time from run failure to useful diagnosis/action.
- Recommendation approve/execute/dismiss rate segmented by confidence and evidence availability.
- Keyboard/a11y support incidents and route-specific frontend errors.

Use these measures to find friction, not to pressure users into more automation.

## Research register

Standards define minimum obligations; peer-reviewed and authoritative UX research informs design
choices beyond compliance.

| ID | Source | Type | Applied to |
|---|---|---|---|
| R1 | [W3C — Understanding WCAG 2.2 SC 1.4.3 Contrast (Minimum)](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html) | Accessibility standard guidance | Theme contrast and automated contrast gate |
| R2 | [W3C — Understanding WCAG 2.2 SC 1.4.1 Use of Color](https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html) | Accessibility standard guidance | Status/chart cues independent of color |
| R3 | [W3C — Understanding WCAG 2.2 SC 1.4.10 Reflow](https://www.w3.org/WAI/WCAG22/Understanding/reflow.html) | Accessibility standard guidance | 320 CSS px reflow and contained table scrolling |
| R4 | [W3C — Understanding WCAG 2.2 SC 2.4.7 Focus Visible](https://www.w3.org/WAI/WCAG22/Understanding/focus-visible.html) | Accessibility standard guidance | Keyboard focus visibility |
| R5 | [W3C — Understanding WCAG 2.2 SC 2.5.8 Target Size (Minimum)](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html) | Accessibility standard guidance | Dense controls and touch/zoom targets |
| R6 | [W3C — Understanding WCAG 2.2 SC 3.3.4 Error Prevention](https://www.w3.org/WAI/WCAG22/Understanding/error-prevention-legal-financial-data.html) | Accessibility standard guidance | Reversible, checked, or confirmed data changes |
| R7 | [WAI-ARIA APG — Tabs Pattern](https://www.w3.org/WAI/ARIA/apg/patterns/tabs/) | Authoritative interaction pattern | Keyboard tabs, selection, and focus |
| R8 | [WAI-ARIA APG — Modal Dialog Pattern](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/) | Authoritative interaction pattern | Confirmation focus and modal semantics |
| R9 | [WAI-ARIA APG — Grid Pattern](https://www.w3.org/WAI/ARIA/apg/patterns/grid/) | Authoritative interaction pattern | Interactive table/grid keyboard model |
| R10 | [WAI — Forms Tutorial](https://www.w3.org/WAI/tutorials/forms/) | Authoritative accessibility guidance | Labels, instructions, validation |
| R11 | [Nielsen Norman Group — 10 Usability Heuristics](https://www.nngroup.com/articles/ten-usability-heuristics/) | Research-based usability guidance | Status visibility, prevention, recovery, recognition |
| R12 | [Nielsen Norman Group — Progressive Disclosure](https://www.nngroup.com/articles/progressive-disclosure/) | Research-based design guidance | Advanced forms and broad task domains |
| R13 | [Nielsen Norman Group — Data Tables: Four Major User Tasks](https://www.nngroup.com/articles/data-tables/) | Research-based design guidance | Find, compare, inspect/edit, and act on records |
| R14 | [Nielsen Norman Group — Dashboards: Making Charts and Graphs Easier to Understand](https://www.nngroup.com/articles/dashboards-preattentive/) | Research-based dashboard guidance | KPI hierarchy and dashboard purpose |
| R15 | [Nielsen Norman Group — Minimize Cognitive Load](https://www.nngroup.com/articles/minimize-cognitive-load/) | Research-based design guidance | Simplification, grouping, recognition |
| R16 | [Nielsen Norman Group — Response Times: The 3 Important Limits](https://www.nngroup.com/articles/response-times-3-important-limits/) | Human-factors guidance | Loading/progress feedback thresholds |
| R17 | [Nielsen Norman Group — Confirmation Dialogs Can Prevent User Errors](https://www.nngroup.com/articles/confirmation-dialog/) | Research-based design guidance | Selective, consequence-specific confirmation |
| R18 | [Nielsen Norman Group — Breadcrumbs: 11 Design Guidelines](https://www.nngroup.com/articles/breadcrumbs/) | Research-based navigation guidance | Location and hierarchical recovery |
| R19 | [GOV.UK Design System — Error Summary](https://design-system.service.gov.uk/components/error-summary/) | Research-backed government design standard | Focused summary linked to invalid fields |
| R20 | [IBM Carbon Design System — Empty States](https://carbondesignsystem.com/patterns/empty-states-pattern/) | Enterprise design-system guidance | Context, next step, and corrective action |
| R21 | [Ben Shneiderman — The Eight Golden Rules of Interface Design](https://www.cs.umd.edu/~ben/goldenrules.html) | Foundational HCI guidance | Feedback, closure, reversal, user control |
| R22 | [Hick, W. E. (1952), “On the rate of gain of information”](https://doi.org/10.1080/17470215208416600) | Peer-reviewed foundational HCI paper | Choice complexity and decision time |
| R23 | [Fitts, P. M. (1954), “The information capacity of the human motor system...”](https://doi.org/10.1037/h0055392) | Peer-reviewed foundational HCI paper | Target acquisition and control sizing |
| R24 | [Cleveland & McGill (1984), “Graphical Perception...”](https://www.tandfonline.com/doi/abs/10.1080/01621459.1984.10478080) | Peer-reviewed JASA paper | Accurate visual encodings and chart selection |
| R25 | [Lee & See (2004), “Trust in automation: designing for appropriate reliance”](https://pubmed.ncbi.nlm.nih.gov/15151155/) | Peer-reviewed Human Factors review | Confidence, provenance, context, calibrated trust |
| R26 | [Nielsen Norman Group — The Aesthetic-Usability Effect](https://www.nngroup.com/articles/aesthetic-usability-effect/) | Research-based usability guidance | Separate perceived ease/appeal from observed task performance |
| R27 | [Nielsen Norman Group — 5 Principles of Visual Design in UX](https://www.nngroup.com/articles/principles-visual-design/) | Research-based visual-design guidance | Scale, hierarchy, balance, contrast, and Gestalt grouping |
| R28 | [Lavie & Tractinsky (2004), “Assessing dimensions of perceived visual aesthetics of web sites”](https://dl.acm.org/doi/10.1145/985692.985701) | Peer-reviewed IJHCS paper | Classical and expressive visual-aesthetic quality |
| R29 | [Tractinsky, Katz & Ikar (2000), “What is beautiful is usable”](https://doi.org/10.1016/S0953-5438(00)00031-X) | Peer-reviewed Interacting with Computers paper | Relationship between aesthetics and perceived usability |
| R30 | [Lindgaard et al. (2006), “Attention web designers: You have 50 milliseconds to make a good first impression!”](https://www.tandfonline.com/doi/abs/10.1080/01449290500330448) | Peer-reviewed Behaviour & Information Technology paper | Speed and stability of visual first impressions |
| R31 | [Heer & Bostock (2010), “Crowdsourcing Graphical Perception: Using Mechanical Turk to Assess Visualization Design”](https://idl.cs.washington.edu/files/2010-MTurk-CHI.pdf) | Peer-reviewed CHI study | Encoding accuracy, gridline contrast, and chart sizing |
| R32 | [ISO 9241-11:2018 — Ergonomics of human-system interaction: Usability](https://www.iso.org/standard/63500.html) | International standard | Usability as an outcome of use in a specified context |
| R33 | [Nielsen Norman Group — Usability 101: Introduction to Usability](https://www.nngroup.com/articles/usability-101-introduction-to-usability/) | Research-based usability guidance | Utility, learnability, efficiency, memorability, errors, and satisfaction |
| R34 | [Nielsen Norman Group — Cognitive Walkthroughs: A Method to Assess Learnability](https://www.nngroup.com/articles/cognitive-walkthroughs/) | Structured inspection method | Step-level first-use goals, discovery, mapping, and feedback |
| R35 | [Nielsen Norman Group — How to Measure Learnability of a User Interface](https://www.nngroup.com/articles/measure-learnability/) | Research-based measurement guidance | First use, learning-curve steepness, and expert plateau |
| R36 | [Nielsen Norman Group — Card Sorting: Uncover Users’ Mental Models for Better Information Architecture](https://www.nngroup.com/articles/card-sorting-definition/) | Research-method guidance | User-defined grouping, labels, card and participant guidance |
| R37 | [Nielsen Norman Group — Tree Testing: Fast, Iterative Evaluation of Menu Labels and Categories](https://www.nngroup.com/articles/tree-testing/) | Research-method guidance | Navigation findability without visual-design confounds |
| R38 | [Hart & Staveland (1988), “Development of NASA-TLX (Task Load Index): Results of Empirical and Theoretical Research”](https://doi.org/10.1016/S0166-4115(08)62386-9) | Peer-reviewed human-factors method | Multidimensional subjective workload measurement |
| R39 | [Nielsen Norman Group — Measuring Perceived Usability](https://www.nngroup.com/articles/measuring-perceived-usability/) | Quantitative UX guidance | SEQ/post-task and SUS/post-test use beside performance data |
| R40 | [Lewis, Utesch & Maher (2013), “UMUX-LITE: when there’s no time for the SUS”](https://doi.org/10.1145/2470654.2481287) | Peer-reviewed CHI paper | Validated concise perceived-usability measure |
| R41 | [Rodden, Hutchinson & Fu (2010), “Measuring the User Experience on a Large Scale: User-Centered Metrics for Web Applications”](https://research.google/pubs/measuring-the-user-experience-on-a-large-scale-user-centered-metrics-for-web-applications/) | Peer-reviewed CHI paper | HEART goals-signals-metrics framework |
| R42 | [Google web.dev — Web Vitals](https://web.dev/articles/vitals) | Authoritative performance guidance | Field measurement and p75 LCP, INP, and CLS thresholds |
| R43 | [W3C — Making Content Usable for People with Cognitive and Learning Disabilities](https://www.w3.org/TR/coga-usable/) | W3C supplemental accessibility guidance | Clear purpose, familiar patterns, help, and cognitive accessibility |
| R44 | [W3C WAI — Involving Users in Evaluating Web Accessibility](https://www.w3.org/WAI/test-evaluate/involving-users/) | W3C evaluation guidance | Disabled-user participation alongside standards review |
| R45 | [Nielsen Norman Group — Field Studies](https://www.nngroup.com/articles/field-studies/) | Contextual research guidance | Natural context, interruptions, vocabulary, workarounds, and collaboration |
| R46 | [Nielsen Norman Group — Diary Studies: Understanding Long-Term User Behavior and Experiences](https://www.nngroup.com/articles/diary-studies/) | Longitudinal research guidance | Habits, repeated activities, changing perceptions, and cross-channel work |
| R47 | [Nielsen Norman Group — Turn User Goals into Task Scenarios for Usability Testing](https://www.nngroup.com/articles/task-scenarios-usability-testing/) | Usability-test guidance | Realistic, actionable, non-leading task construction |
| R48 | [Nielsen Norman Group — Quantitative Studies: How Many Users to Test?](https://www.nngroup.com/articles/quantitative-studies-how-many-users/) | Quantitative UX guidance | Sample size, variability, confidence intervals, and comparative metrics |
| R49 | [Nielsen Norman Group — Competitive Usability Evaluations: Learning from Your Competition](https://www.nngroup.com/articles/competitive-usability-evaluations/) | Comparative research guidance | Same-task competitor benchmarking and counterbalanced presentation |
| R50 | [Nielsen Norman Group — Information Scent: How Users Decide Where to Go Next](https://www.nngroup.com/articles/information-scent/) | Information-foraging guidance | Predictive labels, surrounding context, and destination value |
| R51 | [Nielsen Norman Group — UX Debt: How to Identify, Prioritize, and Resolve](https://www.nngroup.com/articles/ux-debt/) | UX management guidance | Tracking the compounding cost of deferred experience problems |
| R52 | [Nielsen Norman Group — Error-Message Guidelines](https://www.nngroup.com/articles/error-message-guidelines/) | Research-based content guidance | Visible, precise, human-readable, recovery-oriented errors |
| R53 | [W3C Internationalization — Internationalization Quick Tips for the Web](https://www.w3.org/International/quicktips/) | W3C internationalization guidance | Unicode, language, direction, presentation separation, and locale readiness |
| R54 | [Nielsen Norman Group — Mental Models in UX Design](https://www.nngroup.com/articles/mental-models/) | Foundational HCI guidance | Aligning the interface model with user predictions |
| R55 | [Nielsen Norman Group — Analytics for UX](https://www.nngroup.com/articles/analytics-user-experience/) | UX measurement guidance | Analytics for issue indication, investigation, and triangulation |
| R56 | [Nielsen Norman Group — Why You Only Need to Test with 5 Users](https://www.nngroup.com/articles/why-you-only-need-to-test-with-5-users/) | Formative usability-test guidance | Small iterative qualitative rounds rather than one unrevised large study |

## Final recommendation

Do not start with another visual redesign. The current design language is already credible. Spend
the next improvement cycle on:

1. safety boundaries for high-impact actions [R6, R8, R17, R21];
2. complete keyboard/screen-reader/chart equivalence [R2, R4, R5, R9, R24];
3. deterministic accessibility and visual regression [R1–R5, R11];
4. URL-backed operational context [R7, R11, R18];
5. structured forms and error recovery [R10, R19, R20];
6. evidence/provenance for automated conclusions [R11, R25];
7. a stronger, ownable visual hierarchy rather than more card decoration [R27–R30];
8. common-scale chart encodings, direct labels, accessible equivalents, and honest uncertainty
   [R2, R9, R24, R25, R31];
9. repeated top-task testing for learnability, memorability, workload, interruption recovery, and
   role-specific efficiency [R33–R39, R47, R48, R56];
10. disabled-user research and production performance evidence, not checklist-only claims
    [R42–R44];
11. field, diary, HEART, and privacy-safe analytics evidence that Cryo displaces workarounds and
    sustains value over time [R41, R45, R46, R55]; and
12. an owned UX-debt register and same-task competitive benchmark [R49, R51].

Those changes address the lowest scoring dimensions while preserving Cryo’s strongest advantage:
an unusually clear, explainable, and operator-centered maintenance workflow.
