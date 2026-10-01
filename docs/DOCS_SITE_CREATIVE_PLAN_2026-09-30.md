# Cryo docs site — creative enhancement plan

Status: plan only, no implementation. Scope: `site/`. This is the merged
version of the original Claude/Anthropic-narrow plan and the broader
2025-2026 dev-tool survey. The original plan's structure and 5 ranked
gaps are preserved as §A–§D; §E–§K are the broader research and
opinionated recommendations.

---

## What this is based on

Three research threads, plus direct inspection of the current site
(screenshots, not guesses):

1. **How Anthropic structures Claude's own docs** (platform.claude.com,
   built on Mintlify). Fetched the `intro` page directly. Patterns:
   `<Steps>` numbered sequences with one-task-per-step + one-link-per-step,
   `<CardGroup>` 2/3-column icon grids with one-line descriptions,
   comparison tables at the top before any prose, consistent icon
   vocabulary, every section ends with a clear "go here next" link.

2. **Broader 2025-2026 dev-tool / OSS site survey** (the new research
   brought in by this merge). Sites surveyed with live fetches include
   Stripe, Linear, Vercel, Tigris, PostHog, ClickHouse, MotherDuck,
   Temporal, Inngest, dbt Labs, Dagster, Kestra, Polar.sh, Modal, Resend,
   Replicate, Tailscale, Sentry, Cal.com, Cloudflare Workers, Plaid,
   Convex, Clerk, Supabase, Neon, Fly, Turso, Sourcegraph, Hugging Face,
   LangChain, LlamaIndex, Mermaid, Starlight, Pagefind, amoro.apache.org,
   docs.lakefs.io. Full brief in `references/design-research.md` (skill)
   and the research subagent transcript.

3. **Direct inspection of the current site** via 18 headless screenshots
   (9 pages × 2 themes) and DOM probes. The site is in a verified-passing
   state: 134/134 inline charts mount, 12/12 Archify iframes render, 9/9
   pages have populated on-this-page TOCs, 0 page errors, 0 console errors.

### What reads as "creative / polished" vs. "generic-AI-template" (from research)

- Sites that stood out **explicitly avoided "obvious tech colours and fonts"**
  by supplying a brand pack up front. Cryo already has this
  (`docs/brand/CRYO_BRAND_GUIDE.md`), which most projects in the survey
  had to invent mid-build. Real head start, not a gap.
- Winning dashboards use **real interactive charts** (Recharts/Chart.js:
  tooltips, CSV export, hover states) — not static images. Cryo's site
  already has a hand-rolled SVG chart engine (`site/assets/js/charts.js`,
  15 chart kinds) — the gap is **interactivity**, not chart existence.
- **Motion was purposeful and minimal** — smooth scroll, one settle-in
  animation on load — never continuous/ambient. Matches the brand guide's
  own rule (§10: "at most one purposeful animation ... under 1.2s").
- The consistent finding across every source: **the underlying content
  quality is what people actually praise**. The tool renders it well, it
  doesn't invent credibility. Cryo's Confluence material (the existing
  "why maintenance matters" section already ported to `maintenance/`) is
  exactly this kind of real, specific content — more of it should
  surface on the site, sanitized, rather than more illustrative-only
  placeholder charts.

---

## What's already working (do not redo)

Confirmed today via real screenshots and DOM probes, not assumption:

- Brand-compliant palette, typography, dark mode, the Core illustration
  motif (loose tiles → solid blocks, with one survey-orange marker).
- 15 working chart kinds covering most concepts already: `rail`, `bars`,
  `bands`, `figure` (grouped nodes + edges), `matrix`, `gauge`, `funnel`,
  `sparkline`, `receipt`, `timeline`, `donut` (with `compact`), `waterfall`
  (with `compact`), `compare` (paired bars + delta), `dot-matrix` (graded
  with legend), `treemap`, plus `core` mini-mark and `layer-rule` divider.
- Archify-generated interactive architecture diagrams embedded via iframe,
  finalized with passing receipts (`validate=pass, deliver=pass,
  check=pass, browser-check=pass`) and a 5/5 themes light/dark sync.
- Cross-page "Read next" navigation on every inner page, on-this-page
  right-rail scrollspy, terminal-styled code blocks with copy buttons,
  numbered sidebar nav, layer-line section dividers, dotted-grid
  background, dark-mode contrast overrides.
- Client-side search built from a build-time JSON index, with arrow-key
  navigation, lazy-loaded on first input.

Two real bugs found and fixed in earlier sessions: the homepage hero
had a duplicated/overlapping caption (chart engine rendering its own
caption text on top of hand-written label markup), and the architecture
page's treemap chart drew text labels that overflowed into neighboring
tiles on narrow slices. Both fixed in `site/index.html` and
`site/assets/js/charts.js`.

This plan is **deliberately not a rebuild**. The foundation is sound;
what's missing is the specific structural/interactive patterns below.

---

## Section A. Gaps from the original plan (preserved)

### A.1. No "which path is for me" comparison at the top of the homepage

Claude's docs open with a 2-column table (Messages API vs. Managed Agents)
before any prose. Cryo's homepage opens with a hero, then a 4-step
"observe/decide/run/prove" section — good, but never answers "is Cryo
the right tool for *my* situation" the way a comparison does. The
existing reference-page comparison matrix (Cryo/DIY/Amoro/LakeOps/
Managed cloud) is buried on the reference page, page 8 of 9.

**Move a condensed version of it above the fold on the homepage** —
3-4 rows, not the full matrix — right after the hero, before the 4-step
section. Reuse the `dot-matrix` chart primitive that's already built.

### A.2. No `<Steps>`-style guided path

Cryo's quickstart page is a single terminal block. Claude's "recommended
path" pattern — numbered steps, each with one link, ordered by what a
genuinely new user needs *next* — doesn't exist anywhere on the site.

**Add a 5-step "Recommended path" component** to the homepage or
quickstart page: (1) run the quickstart, (2) connect a real source,
(3) assign a preset, (4) watch a run, (5) read a receipt. Each step:
one sentence, one link, using the existing card styling already on
`reference/index.html`'s "Read next" section — don't invent new CSS.

### A.3. Charts are static SVG, not interactive

Every chart in `site/assets/js/charts.js` renders once and sits inert —
no hover tooltips beyond native SVG `<title>`, no click-to-filter, no
live data. The Claude-Code-built dashboards that got singled out as
impressive all had **hover tooltips with real values and click
interactions**.

**Concrete, scoped addition**: give the `bars`, `waterfall`, `compare`,
`treemap`, `donut`, and `timeline` chart kinds a shared hover-tooltip
primitive (one small function in `charts.js`, reused across kinds) that
shows the exact value on `:hover`/focus, keyboard-accessible, with
proper `aria-describedby`. This is the single highest-leverage visual
upgrade available without inventing new chart types.

### A.4. Homepage lacks a top-level "why Cryo, in one comparison" motif

Section 7.3 of the brand guide has a specific illustration for
"Choosing a table from the fleet" (a rack of core segments, one lifted
out) that isn't used anywhere on the site yet — every current illustration
use is the core-sample motif itself.

**Consider this for the homepage's evidence section**, replacing one of
the generic donut/gauge placeholders with an on-brand illustration that
hasn't been used yet, per the brand guide's own motif library. Low
priority — needs a human to actually draw or commission it per the
brand guide's "do not generate the final logo/symbol with AI" rule; the
existing donut/gauge placeholder is fine until then.

### A.5. Confluence content only partially ported

Only `maintenance/index.html` got the sanitized "why maintenance matters"
content this session. The competitive-landscape quadrant chart, the
AI-scope table (QPP/Plan Verifier/layout recommendations), and the
research-grounding table (AutoComp, WAIR, PTO citations) from the same
source page are real, citable, non-production content that never made
it onto any page.

**These belong on `concepts/index.html` or a new `research/index.html`**
— they're exactly the kind of "real content over illustrative
placeholder" upgrade the second research thread flagged as what
actually reads as credible.

---

## Section B. What I am explicitly not proposing (preserved)

- A rebuild of the chart engine, navigation, or CSS system — all sound.
- Copying Mintlify's visual style wholesale — Cryo's brand guide is
  more specific and intentional than a generic docs-platform default;
  following it is the right call, not a limitation to work around.
- Any new real production numbers, competitor disparagement, or claims
  outside `docs/COMPETITIVE_STRATEGY_AMORO_LAKEOPS_2026-09-28.md`'s
  rules — same constraint as before.

---

## Section C. Suggested order from the original plan (preserved)

1. Hover-tooltip primitive for `bars`/`waterfall`/`compare`/`treemap`/
   `donut`/`timeline` (§A.3) — contained, one function, testable in
   isolation, highest visual payoff per line of code changed.
2. Homepage comparison table above the fold (§A.1) — content already
   exists on the reference page, this is presentation, not new writing.
3. "Recommended path" Steps component (§A.2) — reuses existing card CSS.
4. Port the remaining Confluence content (§A.5) to `concepts/index.html`.
5. New illustration motif for the homepage evidence section (§A.4) —
   lowest priority, needs a human to actually draw or commission it
   per the brand guide's "do not generate the final logo/symbol with
   AI" rule.

---

## Section D. New from the merge — broader creative research

What follows is the wider research base. Each item is one concrete
recommendation with named examples, ordered by ROI for Cryo given the
brand guide, AGENTS.md constraints, and "no build step" choice the
project has already made.

### D.1. Hero — three archetypes, ranked by Cryo fit

The first subagent surveyed the hero archetype space more carefully and
arrived at a ranked recommendation that the second subagent missed.
Cryo fits **three** hero archetypes well, in this order of strength:

#### D.1.a. STRONGEST — Customer-mosaic, retargeted at contributors

Polar.sh's homepage hero is a dense grid of real customer-name chips
animating in. No logos, no stock photos, no testimonials. The chips
*are* the customer list rendered as design. WorkOS does the same with
logos in their actual brand colours. Cal.com does a similar thing on
the /customers page.

**Why it works**: removes the "is this real?" hesitation without looking
needy. Scales: 10 contributors reads as "real", 50 reads as "growing",
500 reads as "ubiquitous". Honest (it's literally a public contributor
list).

**Why it fails**: an empty grid is worse than no grid. Needs the
contributor list to be real, current, and opt-in. Some readers find
the dense grid visually noisy on mobile.

**Cryo fit**: **STRONG.** Cryo is OSS, so a customer-mosaic of
**contributors** does the same job — real GitHub usernames, animated
in, with one orange marker on a contributor-of-the-week. Reads as
honest (public contributor list, no fakery), avoids stock photos
(banned by brand), stays inside the palette (each contributor chip is
a Frost or Firn tile), and is **distinctive** — no other Iceberg
maintenance tool ships this. Place it **below** the type-driven
headline, not in place of it, so the positioning still has somewhere
to land.

#### D.1.b. STRONG — Type-first, one careful illustration

Linear (full-bleed Inter/Berkeley-Mono type, dark canvas, one quiet
illustration, almost no chrome), Vercel (typographic homepage, gradient
meshes exist only on subpages), Resend (centered type + animated
envelope, deliberately plain), Framer (bold type + motion-as-design).

**Why it works**: the strongest pattern of 2025-2026 for serious dev
tools. Type is the brand. The illustration earns its place or isn't
there. Reads as confident because there's nothing to hide behind.

**Why it fails**: depends entirely on type quality and a strong
wordmark. Badly-set type destroys the effect. Doesn't demo anything.

**Cryo fit**: **STRONG.** IBM Plex Sans SemiBold at 56-64 px on Snow
with one small Slate eyebrow line and the Core on the right per brand
§10 is **already this archetype**. Don't overthink it. The risk is
over-decorating — the moment we add a "live counter" or a "stats band"
or a "floating card" we're back in generic-SaaS territory.
**Recommendation: type-first hero, Core on the right, Core-blue button
+ text link, period.**

#### D.1.c. CONDITIONAL — Live-data hero (a TRAP for Cryo)

Tigris (real request-volume chart animating against time, monospace
numbers), ClickHouse (rows-per-second chart on load, yellow accent),
PlanetScale legacy (query-throughput chart).

**Why it works**: if the numbers are real, this is the strongest "we
do the thing" signal in the pattern library. If the numbers are
mocked, it's the worst.

**Why it fails for Cryo specifically**: brand guide §11 explicitly
forbids invented performance numbers on marketing surfaces. A live
"compaction rate" or "files compacted across all Cryo deployments"
chart would either (a) be fake (banned), (b) leak production data
(banned), or (c) be a single number from one deployment (becomes
stale the moment the deployment moves).

**The one safe variant**: a *visual, not measured* animation showing
the Core's tiles settling into blocks (brand §10's "one purposeful
animation, under 1.2 s, once"). That's the live-data hero, re-stated
in the brand's own idiom. **Keep the motion; lose the number.** This
is already implemented.

#### D.1.d. STRONG (with the brand-correct shot) — Split-with-console

Stripe (left: three short value props, right: realistic API console
with realistic code), Linear variations, Supabase (emerald-accented).

**Why it works**: the product shot is doing the explaining. The copy is
short.

**Why it fails**: if the product shot is generic, the page reads as
every other dev-tool site. The differentiator is *which* shot, not
*whether* a shot. Stripe wins because the shot is recognisably Stripe.

**Cryo fit**: brand §10 already locks "hero: left text, right the
Core". The catch: the "console" half of the pattern wants to be a real
Cryo UI screenshot. Brand §10 also bans recolouring UI screenshots.
So if we ship a screenshot, it's the existing cobalt-accent product
UI inside a plain Firn-bordered frame (per §10) — honest and on-brand.

**Recommendation: the homepage hero keeps the Core on the right (no
fake UI), but on the maintenance page or quickstart page, the right
side becomes a real product screenshot. Two different heroes, two
different jobs.**

#### D.1.e. BANNED for homepage — Interactive embedded widget

Cal.com (working calendar widget), tldraw (live infinite-canvas demo),
Replit 2025 (embedded IDE).

Cryo is a backend platform; a "click here to maintain a table" widget
needs a live backend, which we don't ship on the docs site, and a
backend-mode fake risks looking dishonest. **MAYBE for the architecture
page**: the existing Archify embed is exactly this pattern in our
idiom.

#### D.1.f. BANNED — Live CLI terminal as the hero

The second subagent recommended this (asciinema recording of
`cryo plan --table events` as the homepage hero centerpiece). After
the merge, this is demoted to a **section-level demo** (see §D.5), not
a hero. The reason: a CLI terminal as the hero anchors the page on
"this is a CLI tool" when Cryo's actual value is the read-side /
write-side split + the plan table. The terminal is great evidence;
it's not the positioning.

#### D.1. Summary hero recommendation

**Homepage**: D.1.a (contributor mosaic) BELOW the D.1.b (type-first
hero + Core on the right). Contributor mosaic as the trust strip
immediately under the hero, not inside it.

**Maintenance / Quickstart pages**: D.1.d (split-with-console) — real
product screenshot on the right inside a Firn-bordered frame, no
recolouring.

**Architecture page**: D.1.e (Archify embed, already shipped).

#### D.1. Anti-patterns explicitly rejected

Gradient mesh blobs (Vercel 2022, Stripe 2022, all removed by 2024),
autoplay hero video (Vercel removed), "big number" hero (only when
there's a publicly defensible source — Cryo doesn't have one yet),
fake UI screenshot (no UI to fake, and a fake reads as every other
dev-tool site), 3D product mockups (was hot 2022-23, now reads as
"designer got bored"), neumorphism, glassmorphism, hover-to-reveal
screenshots (a11y disaster), AI-sparkle icons (banned by brand §2).

### D.2. Section patterns

| Pattern | Verdict for Cryo |
|---|---|
| Live counters / shipped-today strip | **Yes, but build-time, NOT in the hero.** A "State of the project" strip with GitHub stars, commits in last 30 days, releases/year — pulled from GitHub API at build, with a "Updated 2026-09-30" timestamp in monospace. Sentry, ClickHouse, PostHog, Dagger, tRPC all do this. Pull at build time, not client-side (client-side fetches get rate-limited and look broken). Placement: footer band or one tight strip below the hero, NOT in the hero. |
| Sticky side-notes / margin annotations | **Yes.** Already have the right-rail scrollspy. Add a separate "Notes & caveats" left-margin callout for non-obvious gotchas ("Requires Hive metastore or REST catalog — see Catalog setup"). One callout per major section, not on every paragraph. Stripe Docs, dbt Labs. |
| Pull-quote testimonials | **No.** Brand bans people. Use small "From the field" code-comment-styled blocks instead — pull a real anonymized GitHub issue or PR review comment that shows the tool being used, in monospace. Both proof and personality. |
| In-browser shell terminals | **No.** A terminal that can't actually compile/run a plan table is worse than no terminal. Use asciinema / VHS recordings of real `cryo` runs. |
| Interactive API playgrounds | **No.** Cryo doesn't expose a public API. Don't fake one. |
| Scroll-driven storytelling | **One short section max** on the Architecture page, animating the cutaway plate (Snow → Frost → Firn → Glacier → Core blue layers of the stack). Don't pin for more than ~80vh. Apple product pages, Vercel "How Turborepo works", Linear changelog — all do short pins; long pins are anti-pattern. |
| Big-number callouts | **Yes, in body, sourced.** On the homepage "Why Iceberg needs maintenance" section. e.g., "Iceberg metadata grows ~10% per 1M writes — 50M writes/day = 5M dead metadata files in a week." Cite a public benchmark. Don't claim Cryo's own number unless it's from a published run. |
| Drag-the-slider | **STRONG fit, BEST demo archetype for Cryo.** The single most brand-correct interactive demo: "files to compact" slider, the Core visually compacts in step. Beautiful, on-brand (the Core *is* this image), no backend. See §D.5 for full recommendation. |
| "X is Y because Z" infographic | **Yes, on Architecture page.** One diagram per concept (Snapshot lifecycle, Compaction strategy, Catalog role, Snapshot expiry). Each in the established flat-vector cutaway style. No infographic generators, hand-author every label. |
| Comparison rail ("Pick the one you need") | **Yes — already in §A.1.** Claude docs home, Vercel framework matrix, Astro integrations. Trimmed matrix lifted from the reference page. |
| "Read next" / "Steps" pattern | **Yes — already in §A.2.** Every page ends with "go here next". Numbered, one task per step, one link each. |
| Click-to-cycle screenshots | **Maybe.** A 3-4 frame storyboard of a table going from "many small files" → "compacted" → "expired snapshots" → "clean state", with one marker on the active frame. Low priority, requires real screenshots. |
| Hover-to-reveal screenshots | **NO.** Fights a11y, fights keyboard users, ages the moment the screenshot changes. |

### D.3. Diagram systems — combine inline SVG with tool output

**Best-practice rules from the survey** (Stripe, Temporal, dbt Labs,
Tigris, Dagster, Kestra, MotherDuck, ClickHouse):

| Tool | Wins at | Cryo verdict |
|---|---|---|
| Inline SVG (hand-authored) | Diagrams on >2 pages, themed diagrams, brand-palette diagrams. The only way to keep glacier colors and IBM Plex labels consistent. | **Default. Already correct.** |
| Mermaid | Auto-generated sequence/flow from data. Styling awkward in dark mode; labels fight IBM Plex. | **OK in blog posts only**, not in core docs. If used, restyle via `themeVariables` — but accept rendering drift and review every chart. |
| excalidraw / hand-drawn | Sourcegraph, a few Temporal blog posts. Warm, casual. | **Wrong.** Brand says scientific, not casual. |
| Images / PNGs | Screenshots of *external* things (Spark UI, Nessie UI, AWS console). | **Never for own diagrams.** |
| Iframes (Archify, Mermaid Live Editor, tldraw) | *Interactive* diagrams (zoom/pan on architecture pages). | **Right answer for diagrams that need to be edited by non-engineers.** Already adopted. |
| Interactive JS (D3, react-flow) | Lineage / DAG graphs in dedicated pages. | **OK on exactly two pages max**: a future "Explore a Compaction DAG", and the Architecture overview if it benefits from zoom/pan. Until those exist, no interactive JS. |

**Chart engine additions** — three kinds the current 15 are missing:

1. **Stacked-bar over time** (compaction history per table, week-by-week).
2. **Sparkline-in-table** (each row of a tables list carries a 60-day
   sparkline of bytes/grade — the best table-row pattern seen on
   Tigris, Datadog, and Stripe).
3. **Radial / polar chart** for "disk usage by partition" (a single
   circle showing the wedge for each partition's share).

**Skip**: pie (always wrong), 3D (always wrong), radar (too gamey).

### D.4. Trust & social proof for OSS without marketing smell

What works (PostHog, Cal.com, tldraw, Inngest, Tigris, Resend, ClickHouse):

- Real-time numbers from public repos — GitHub stars, contributors,
  releases. Build-time pull.
- "Shipped in production at" with named companies, only when you can
  actually name them.
- Public benchmark numbers with the dataset name and the commit hash
  of the code that ran them (ClickHouse publishes this religiously).
- Issue / PR screenshots — real, anonymized if needed.
- Public Discord/Slack member count + join link.
- Docker Hub pulls, PyPI downloads, GitHub release download counts.

What fails (and Cryo shouldn't do):

- Stock-photo testimonials (banned by brand guide anyway).
- "Trusted by 10,000 developers" with no logos.
- Award badges (Product of the Day, G2) — anti-signal for OSS.
- Anonymous quotes. If you can't put a name and a link, don't print it.

**Recommendation**: a single "State of the project" strip on the
homepage and README-style page — stars, contributors, releases (from
GitHub API, build-time). Plus "Tested against Iceberg v1.4, v1.5, v2"
with version-pinned commit hash. Plus one real production case study,
written by the user, linked from a blog post — **not** reprinted on
the homepage. No fake testimonials, no award badges, no "as featured
in" wall.

### D.5. Interactive demos — what's realistic for a backend tool

The hard constraint: Cryo is a backend platform. No shipping backend,
no real demo. But there's a real spectrum between "static screenshot"
and "live API".

#### Demo archetypes by effort / honesty trade-off

| Archetype | Example | Effort | Honesty |
|---|---|---|---|
| Static product screenshot in a frame | Most OSS sites | trivial | high |
| Static screenshot with hover annotations | Stripe docs | low | high |
| Pre-rendered animated terminal output | Stripe, Vercel | medium | high |
| Click-to-cycle screenshots of a flow | Cal.com pricing pages | low | medium |
| In-browser shell terminal (pre-scripted) | terminaltrove.com | medium | medium |
| Live API playground (real backend) | Stripe, Twilio | huge | highest |
| WebAssembly port of the real thing | DuckDB-WASM, SQLite-WASM | huge | highest |
| Drag-the-slider demo | (no canonical example) | medium | high |

#### Cryo recommendation, in priority order

1. **Drag-the-slider, see the Core compact** — *the single most
   brand-correct interactive demo in the entire research set.* A
   slider on the "files to compact" axis; the Core SVG visually
   compacts in step — loose tiles become packed rectangles become
   solid blocks. Beautiful, on-brand (the Core *is* this image), no
   backend, no fake. This is the demo that says "Cryo does this" in
   pure brand vocabulary. Host as a small interactive section on the
   maintenance page.
2. **Pre-rendered animated terminal** — hand-craft a 30-second
   realistic-looking CLI session: `cryo scan`, `cryo run`, `cryo
   receipts show <id>`. Each line fades in, the next appears, the
   prompt blinks. Recorded once, played on hover/intersection. Fits
   the brand's "operational" voice. Avoids any backend. Host as
   asciinema `.cast` files in `site/assets/casts/` (Fly.io, Tigris,
   Modal, Resend all do this).
3. **Click-to-cycle flow** — a 3-4 frame storyboard of a table going
   from "many small files" → "compacted" → "expired snapshots" →
   "clean state", with one marker on the active frame. Could be a
   hand-rolled SVG or four screenshots. Low priority, requires real
   screenshots.
4. **DuckDB-WASM "try Iceberg SQL" embed** on a "What is Iceberg
   metadata?" page — clearly labeled as "DuckDB, not Cryo." Pyodide
   can't talk to S3 without CORS; a fake sandbox is worse than no
   sandbox. Cross-link honestly.

#### Empty state — use the Core motif

Every docs site has empty states (no tables, no runs, no data).
Generic "sad cloud" or "empty box" illustrations are overdone and
banned by brand §2 spirit. **Use the Core motif itself** for empty
states: a Core with no segments on the rack (per brand §7.3's "Choosing
a table from the fleet" motif). This is a moment to lean into the
brand asset, not to introduce a new illustration system.

#### Hard NO

- In-browser CLI shells that don't actually compile or run anything.
- Interactive API playgrounds without an API.
- Sandboxes that only work on the marketing site (a lie).
- "Run Cryo in your browser" claims that aren't DuckDB-WASM — Pyodide
  without S3 access is a fake.

### D.6. On-this-page navigation

Already implemented: right-rail scrollspy. **Keep it.**

Mobile fallback not yet built: right-rail → "On this page" dropdown
trigger below the H1 (MDN pattern). Same data, different affordance.
This is the single mobile-specific TODO.

Also add:
- **Bottom-page "Next: Catalog setup →" links** on every doc page.
  Cheapest upgrade you can make to docs navigation.
- **Collapse H3s in the right rail under their H2s** (currently flat).
  Sentry and Stripe both do this; halves visual height of long-page
  TOCs.

Skip: top progress bar (visual noise, no information gain over a
good right rail), breadcrumb-with-jump (overkill for Cryo's flat IA).

### D.7. Search — state of the art for static docs in 2026

| Engine | Where it wins | Cryo verdict |
|---|---|---|
| **Pagefind** (v1.5.2) | Static, zero-config, web-component UI, ~100-300 KB payload for 10k pages, built-in faceting/filtering/sorting, multilingual, indexes non-HTML, Apache-2.0, used by MDN. | **Strongest default for static docs in 2026.** |
| Algolia DocSearch | Free for OSS, hosted, JS-heavy. "Industry standard" but also "industry locked-in." | **Avoid.** Requires Algolia account, JS injection, OSS tier can lag on indexing new pages. |
| Orama | In-memory JS, custom UI. Strong for interactive search experiences. | **Possible.** More flexible UI than Pagefind. |
| MiniSearch | Lightweight, less opinionated UI. | **Possible.** Smaller than Pagefind, more work to theme. |
| FlexSearch | Fastest index size. Weaker ranking. | **Skip.** Worse ranking for the same complexity. |
| Stork | Rust+WASM. Smallest index. Weaker multi-language. | **Skip** unless multi-language ships. |
| Custom JSON indices (current) | Works, but ranking and snippeting are re-invented from scratch. | **Migrate away.** The current custom index ~80 LOC is maintainable but won't match Pagefind's snippet ranking, chunked loading, or faceting. |

**Recommendation**: Pagefind, with one caveat (see §D.10).

### D.8. Motion philosophy

Reference points:
- **Linear** (subtle, 150-250 ms ease-out on hovers and panel reveals,
  no autoplay, no pin >1 viewport). Sets the bar.
- **Vercel** (restrained, slightly more motion, still no autoplay).
- **Sentry** (utilitarian, swap-in-place, no decorative motion).
- **Tigris** (almost static — diagrams appear, nothing animates for fun).
- **Inngest** (confident — one continuous timeline, pinned ~1.5 screens,
  earns it).
- **Cal.com** (playful, bouncy — wrong for Cryo).
- **dbt Labs** (data-driven — numbers count up once when they enter
  the viewport, no decorative motion).
- **Temporal** (workhorse — fast page transitions, instant tab swaps,
  no fanfare).

Cryo fit: **"field scientist, not science fiction."** Cal.com's bouncy
and Inngest's pinned timeline are wrong. Linear's restraint is right.
Tigris's near-static is right but slightly too dry for a brand with
personality.

**Concrete rules**:
- Max animation duration: **200 ms, `ease-out`**. Nothing bounces,
  nothing springs.
- No autoplay video. No looping GIFs.
- No scroll-pinning beyond ~80vh on any single section.
- **One count-up animation** on the homepage number strip (dbt pattern).
- Theme toggle: instant swap, no transition (or ≤100 ms cross-fade).
- Hover states: 1 px outline + color shift, 150 ms. No scale transforms.
- Honor `prefers-reduced-motion: reduce` — disable everything except
  theme swap and number counts.
- Page transitions: none (Sentry, Tigris, Linear all skip).

### D.9. Mobile patterns for long-form docs

What collapses:
- Right-rail TOC → "On this page" dropdown trigger below the H1.
- Top nav → hamburger or single search icon + section name + menu.
- Diagrams (inline SVG) → must have explicit `viewBox` and `width: 100%`.
  Audit every SVG.
- Footnotes / side-notes → inline at point of reference. Mobile users
  don't tap footnote markers.

What stays:
- Code blocks (full-width but horizontally scrollable, with a visible
  "→" indicator at the right edge when overflowing).
- Tables (allow horizontal scroll — don't try to fit-to-screen).
- Image / diagram captions.
- Section anchors (useful for sharing links).

What becomes what:
- Tab groups → stay as tabs but with a visible scroll indicator.
- Accordions → only for FAQ-style sections and long enumerations.
  Don't hide the lede.
- **Swipeable carousels → never.** Even Stripe and Vercel have moved
  away. Anti-pattern in 2026.
- Sticky CTAs (Edit on GitHub, Was this helpful?) → keep, move to
  bottom-of-page only on mobile.

### D.10. What to AVOID in 2026

Patterns that aged poorly (good 2022 dev sites that look stale now):

1. **Animated SVG mesh blobs in the hero.** Stripe 2022, Vercel 2022,
   Linear 2022 — all had them. All removed by 2024-2025. *Banned by
   Cryo's brand guide anyway.*
2. **Auto-playing hero video.** Vercel tried, removed. Muted loop in
   the hero that doesn't show real product behavior — anti-signal.
3. **Sticky CTA bars at the bottom of every page.** "Get started →"
   bar that follows scroll. Sentry, Stripe, Tigris all removed.
4. **Pinned scroll sections >2 viewports.** Users scroll-jump past.
5. **"Trusted by" logo wall with no names.** Stock greyscale logos.
6. **Carousels of testimonials.** No one reads them. No one clicks.
   Killed by everyone by 2024.
7. **Floating chat bubble in the bottom corner.** Intercom/Drift
   widget. Looks like sales pressure. Use Discord/Slack invite link
   in the nav, not a chat widget.
8. **Animated counters that run every time you scroll past them.**
   Once is data viz. Every time is a toy.
9. **Background patterns (grid, dot grid) that move on scroll.**
   Parallax was 2018.
10. **"What is X?" hero copy** ("What is observability?", "What is
    feature management?"). Cal.com, Linear, and Stripe all removed
    in 2023. Tell the user what *you* do, not what the category is.

Brand-specific landmines for Cryo:

- **Anything that looks like an iceberg photograph or a snowflake.**
  Kills the "serious data tool" voice. Brand is *glacier/ice as
  metaphor*, not as illustration.
- **Purple/violet accents.** Banned. Any tool using purple in 2026
  reads as "AI wrapper from 2023" — bad signal.
- **Bouncy illustrations.** Cal.com's style would make Cryo look
  unserious.
- **"Just trust us" metrics.** Number without source or methodology
  is a red flag for data engineers. Cite the source. Always.
- **Gradient text headings.** Banned. Plus they signal "design system
  not yet finished."
- **Glow / drop-shadow on UI elements.** Banned. Reads as "made in
  Figma, never shipped."

---

## Section E. Caveats and decisions still owed

These are *flagged for the owner*, not pre-decided by this plan. The
original brief had no equivalent section; the broader survey surfaced
real tradeoffs that deserve an explicit yes/no.

### E.1. Pagefind vs. keep custom JSON index (tradeoff)

The subagent recommended switching to Pagefind. That recommendation is
sound for an OSS site with no "no build step" constraint — Pagefind is
used by MDN, has ranking + chunked loading + snippet highlighting that
the current 80-LOC custom index won't match.

**BUT** the project has explicitly chosen "pure HTML/JS, no build
step" (re-confirmed in the Phase 9 plan earlier today). Pagefind
*technically* runs at build time only and ships static artifacts — it
doesn't require a runtime bundler. It does require:
- Running `pagefind` CLI as part of the build (a small build step
  inside `scripts/`, not a JS bundler).
- Adding ~100-300 KB of static assets to `site/assets/pagefind/`.
- Replacing the current sidebar search with Pagefind's `<pagefind-search>`
  web component (or keeping the custom dropdown and pointing it at
  Pagefind's index).

**Owner decision needed**: migrate to Pagefind (recommended, removes
~80 LOC of custom search code, better ranking/snippets) or keep the
custom index (no build step, current works fine for 9 pages).

### E.2. Build-time vs. client-side data for the "State of the project" strip

The survey recommends build-time GitHub API pulls for stars / commits
/ releases. That's a build step inside `scripts/build-stats.mjs`,
similar to `scripts/build-search-index.mjs`. It's lightweight, runs
once per deploy, no runtime cost. **Recommend yes**, but flag for
owner — needs a GitHub token (use a read-only public-data token, do
not store in commits per `~/.claude/CLAUDE.md` rules).

### E.3. Asciinema vs. static styled terminals

Asciinema is the strongest differentiator the survey surfaced for
backend tools with no pretty UI. Requires:
- Recording 5-6 `.cast` files (need a real Cryo install to record
  from, even if it's the local demo catalog).
- Adding the asciinema player CSS + JS (~5 KB JS).
- Hosting `.cast` files in `site/assets/casts/`.

**Owner decision needed**: proceed with asciinema (recommended for
strong differentiation) or stay with static styled terminals
(currently good enough).

### E.4. "Recommended path" Steps vs. existing quickstart pattern

Adding a 5-step Recommended Path to the homepage (§A.2) overlaps with
the existing quickstart page. Two options:
- (a) Add the Steps strip *above* the hero on the homepage, as a
  "first-time visitor" pre-hook. The current hero stays for
  returning visitors.
- (b) Replace the homepage's 4-step signal-grid section with a
  Recommended Path, and move the 4-step Observe/Decide/Run/Prove
  cards to a sub-page (probably `concepts/`).

Recommend (a) — less disruptive, lets both serve different audiences
on the same page.

---

## Section F. Updated suggested order

Combining the original §C with the new items from §D–§E, in priority
order (highest ROI first):

1. **Hover-tooltip primitive** (§A.3) — contained, one function,
   testable, highest visual payoff per line of code changed. Do this
   first regardless of other decisions.
2. **Homepage comparison table above the fold** (§A.1) — content
   already exists on the reference page, this is presentation.
3. **Recommended Path 5-step strip** (§A.2 / §E.4 option a) — reuses
   existing card CSS.
4. **Replace homepage terminal hero with an asciinema recording**
   (§D.1, §E.3) — strong differentiator, but needs owner decision +
   needs a real Cryo run to record from.
5. **Add 3 missing chart kinds** (§D.3): stacked-bar over time,
   sparkline-in-table, radial partition.
6. **"X is Y because Z" infographic per concept on Architecture page**
   (§D.2) — one diagram per concept, hand-authored, on-brand.
7. **Build-time "State of the project" strip** (§D.4, §E.2) — GitHub
   stars / commits / releases at build time.
8. **Port remaining Confluence content** to `concepts/index.html`
   (§A.5) — competitive quadrant, AI-scope table, research-grounding.
9. **Pagefind migration** (§E.1) — only if owner says yes to the
   build-step tradeoff.
10. **Mobile TOC dropdown + bottom-page next/prev links** (§D.6).
11. **New illustration motif for homepage evidence section** (§A.4) —
    lowest priority, needs a human to actually draw per brand guide's
    "no AI logos" rule.

---

## Section G. What this plan is explicitly NOT proposing (preserved + new)

Preserved from original:

- No rebuild of chart engine, navigation, or CSS — all sound.
- No copying Mintlify's visual style wholesale.
- No new real production numbers or competitor disparagement.

New (from the survey):

- No autoplay video, no scroll-pinned long sections, no carousels, no
  testimonial pull-quotes (banned by brand), no chat widget, no
  gradient text, no glow.
- No in-browser CLI shell that can't actually compile.
- No interactive API playground without an API.
- No DuckDB-WASM as "Cryo in your browser" — label honestly.
- No "trusted by" logo wall without names.
- No fake or anonymous quotes.
- No mesh blob hero, no "What is X?" copy, no parallax background.

---

## Section H. References

- `docs/brand/CRYO_BRAND_GUIDE.md` — palette, type, illustration
  system, diagram grammar, banned assets.
- `docs/COMPETITIVE_STRATEGY_AMORO_LAKEOPS_2026-09-28.md` — claim rules
  (no modelled savings as money, no competitor disparagement, no
  "Cryo is better" claims).
- `~/.hermes/skills/web-development/oss-docs-site/references/design-research.md`
  — distilled dev-tool/OSS site survey notes (Stripe / Linear /
  Vercel / Tigris / Inngest / Cal.com / Sentry / etc.).
- Subagent research transcripts (broader survey):
  - `/Users/aamir/.hermes/cache/delegation/live/deleg_94p04106/task-0.log`
  - `/Users/aamir/.hermes/cache/delegation/live/deleg_c67c58bf/task-0.log`
- Subagent full briefs:
  - `deleg_94p04106` summary: `/Users/aamir/.hermes/cache/delegation/subagent-summary-0-20260930_201746_474799.txt`
    (28 KB) — broader survey, hero archetypes, section archetypes,
    diagram systems, trust, demos, search, motion, mobile.
  - `deleg_c67c58bf` output: `docs/research/CRYO_DOCS_SITE_RESEARCH_BRIEF_2026-09-30.md`
    (701 lines, 33 KB) — the first subagent's standalone brief that
    surfaced the **Polar.sh contributor-mosaic** finding (§D.1.a) and
    the **live-data-hero trap warning** (§D.1.c) which the second
    subagent missed. Read alongside this plan for the full hero-
    archetype trade-off reasoning and Appendix B site-name-to-URL
    index.
- `docs/HOME_PAGE_PRESENTATION_PLAN_2026-09-29.md` — separate plan
  for the *in-app* Web UI home (`web/src/pages/DatalakeHealth.tsx`),
  not the docs site. Out of scope for this plan.
- `docs/UIUX_COMPARATIVE_REVIEW_seed.md` — seed template for
  comparative review, out of scope.

---

## Section I. Decisions for the owner (do not pre-resolve)

1. **§E.1 Pagefind vs. custom JSON index.** Recommended: Pagefind.
   Counter: keeps the "no build step" promise tighter.
2. **§E.2 Build-time stats from GitHub.** Recommended: yes, with a
   read-only public-data token stored outside the repo.
3. **§E.3 Asciinema as a body demo, drag-the-slider as the headline demo.**
   After the merge, asciinema is recommended as the second-tier body
   demo (right column on the quickstart or maintenance page), not as
   the hero. The headline demo is the drag-the-slider-to-compact-the-
   Core (§D.5 priority 1). Either way, asciinema needs real Cryo runs
   to record from; static terminals already work.
4. **§E.4 "Recommended path" pattern placement.** Recommended: option
   (a) above the hero as a pre-hook.
5. **§A.4 New illustration motif for homepage evidence.** Recommend:
   commission a human designer; do not AI-generate per brand guide
   §6 ("Do not generate the final logo with AI"). Existing donut/gauge
   placeholder stays in the meantime.

Confirm or change each once, then keep them.

---

## Section J. Appendix A — single highest-leverage next iteration, distilled

If the team does exactly one set of things this iteration, it should
be:

1. **Lock the homepage hero** as type-first (§D.1.b) with the Core on
   the right. Add a **contributor mosaic** (§D.1.a) as the trust strip
   *immediately under* the hero, not inside it. Both ship the same
   iteration because they reinforce each other and the engineering is
   shared (DOM-only, no backend, no build step required for the
   contributor mosaic if the contributor list is committed alongside
   the build).

2. **Add the drag-the-slider-to-compact-the-Core demo** (§D.5 priority
   1) on the maintenance page. Pure SVG + a few lines of JS to bind a
   range input to the chart engine's existing `core` primitive. The
   single most brand-correct interactive demo in the entire research
   set; no backend; no fake.

3. **Make on-this-page nav dual-mode**: keep the desktop right-rail
   scrollspy, add a floating "On this page" button at the bottom-right
   of mobile viewports that opens a bottom-sheet TOC (§D.6). Reuses
   the same data and the existing `progress-rail` chart primitive.

That's the smallest set of changes that addresses the most visible
problems, all without introducing new colours, new motion, or new
illustration systems. Every other item in §F is improvement, not
foundation.

## Section K. Appendix B — site-name-to-URL index

For the designer's tab bar:

| Site | URL | Why |
|---|---|---|
| Anthropic Claude docs | https://platform.claude.com/docs | Steps / Cards / comparison table |
| Astro Starlight docs | https://starlight.astro.build | TOC + progress rail + search |
| Cal.com | https://cal.com | Interactive hero |
| ClickHouse | https://clickhouse.com | Live-data hero |
| D2 | https://d2lang.com | Diagram-as-code, themeable |
| Dagger | https://dagger.io | Pipeline hero + OSS trust |
| DuckDB docs | https://duckdb.org/docs/current | Code-first, fast, calm |
| Excalidraw | https://excalidraw.com | Hand-drawn diagrams |
| Framer | https://framer.com | Type + motion |
| Hono docs | https://hono.dev/docs | Code-as-hero |
| Inngest | https://www.inngest.com | Animated flow hero |
| Kroki | https://kroki.io | Diagram gateway |
| lakeFS docs | https://docs.lakefs.io | Reference-style competitor |
| Linear | https://linear.app | Type-first hero |
| Mintlify | https://mintlify.com | Framework used by Claude docs |
| Mermaid | https://mermaid.js.org | Declarative diagrams |
| Orama Search | https://github.com/oramasearch/orama | In-memory search (domain acquired 2025) |
| Pagefind | https://pagefind.app | Static search |
| Polar.sh | https://polar.sh | Customer-mosaic hero |
| PostHog docs | https://posthog.com/docs | Inline app embeds |
| Resend | https://resend.com | Centered-type minimal hero |
| Stork | https://stork-search.net | WASM search |
| Stripe | https://stripe.com | Split-with-console hero, code-first |
| Supabase | https://supabase.com | Grid + dashboard hero |
| Temporal docs | https://docs.temporal.io | Mermaid + hand-drawn mix |
| Tigris | https://tigrisdata.com | Live-data hero + docs |
| tRPC | https://trpc.io | Friendly, code-first |
| Turso | https://turso.tech | Topology-diagram hero |
| Vercel | https://vercel.com | Type + product shot |
| WorkOS | https://workos.com | Customer-mark grid |

---

*Plan complete. No code written. Ready for owner review.*
