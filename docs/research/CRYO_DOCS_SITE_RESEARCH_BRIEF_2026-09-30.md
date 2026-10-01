# Cryo docs site — 2025-2026 visual research brief

Research only. No code. No recommendations about tools to install. Just: which
patterns actually work on real developer-tool / OSS-project sites in 2025-2026,
why, and what fits Cryo's brand constraints
(`docs/brand/CRYO_BRAND_GUIDE.md`).

**Brand constraints recalled here for the designer/engineer reading this brief:**

- Palette: Snow, White, Frost, Firn, Glacier, Core blue, Deep ice, Slate, Survey
  orange (the marker only), Night. Hex-locked.
- Type: IBM Plex Sans + IBM Plex Mono only.
- Illustrations: flat-vector scientific-cutaway plates, the Core (loose tiles →
  packing → solid blocks) is the central memory cue; one orange marker per
  image; no gradients, no glow, no glass, no purple, no people, no logos, no
  fake interfaces inside AI art.
- Motion: at most one purposeful animation per surface, under 1.2 s, respects
  `prefers-reduced-motion`.
- Words on AI-generated images: never. Real text added by hand.
- "The illustrative, not measured" rule applies to anything that looks like a
  benchmark or a number.

---

## 1. Hero archetypes

The hero is the most-scrutinised real estate on a dev-tool site. Five patterns
dominate 2025-2026; only two of them fit Cryo's brand without breaking it.

### 1a. "The product IS the hero" (interactive embedded widget)

- **Cal.com** — https://cal.com — embeds a working calendar/bookable-event
  widget in the hero. The reader picks a date before scrolling.
- **tldraw** — https://tldraw.com — embeds a live infinite-canvas demo in the
  hero. The reader has created a shape before they read a word.
- **Replit (2025 redesign)** — https://replit.com — embedded IDE is the hero
  image. Static, but it looks live.

**Why it works:** zero-friction concept demonstration. Skips the "here's what
our product does" stage entirely. The hand-to-keyboard distance is shortest
imaginable.

**Why it fails:** anchors the page on one feature. Hard to swap the hero when
the product pivots. Devalues long-form positioning copy. Often breaks on mobile
(iframers fall apart, the product changes, the demo crashes).

**Cryo fit: NO for the homepage.** Cryo is a backend platform; a "click here
to maintain a table" widget needs a live backend, which we don't ship on the
docs site, and a backend-mode fake risks looking dishonest. **MAYBE for an
architecture page**: an embedded Archify diagram (already shipped) is exactly
this pattern in our idiom. Promote that to "the hero of the architecture page"
rather than fighting it on the homepage.

### 1b. "Customer-mosaic" (real names in motion)

- **Polar.sh** — https://polar.sh — headline "Meet Polar" + a dense grid of
  customer-name chips animating in. No logos, no stock photos, no testimonials.
  The chips are the customer list rendered as design.
- **Cal.com customers page** — https://cal.com/customers — similar idea: real
  names, real logos, no quotes.
- **WorkOS** — https://workos.com — "Built for" header followed by a giant grid
  of customer marks in their actual brand colours.

**Why it works:** removes the "is this real?" hesitation without looking needy.
The customer list is what readers actually want to scan anyway. It scales: 50
customers reads as "popular", 500 reads as "ubiquitous".

**Why it fails:** if the customer list is thin (< 10), the grid looks
embarrassing. Empty grid is worse than no grid. Needs the customer list to be
real, current, and opt-in. Some readers find the dense grid visually noisy.

**Cryo fit: STRONG.** Cryo is OSS, so a customer-mosaic of *contributors* does
the same job: real GitHub usernames, animated in, with one orange marker on a
contributor-of-the-week. Reads as honest (it's literally a public contributor
list), avoids stock photos (banned), and stays inside the palette (each
contributor chip is a Frost or Firn tile). This is the strongest hero concept
in the brief. Place it **below** the type-driven headline, **not** in place of
it, so the positioning still has somewhere to land.

### 1c. "Full-bleed type, one careful illustration"

- **Linear** — https://linear.app — huge Inter/Berkeley-Mono type, dark canvas,
  one quiet illustration, almost no chrome. The whole page is the hero.
- **Vercel** — https://vercel.com — same shape, white canvas, Geist type,
  product screenshot inset. (Gradient meshes exist on subpages; the homepage
  hero is typographic.)
- **Resend** — https://resend.com — centered type + an animated envelope
  container, deliberately plain.
- **Framer** — https://framer.com — bold type, motion-as-design, navy + electric
  blue.

**Why it works:** the strongest pattern of 2025-2026 for serious dev tools.
Type is the brand. The illustration earns its place or isn't there. Reads as
confident because there's nothing to hide behind.

**Why it fails:** depends entirely on type quality and a strong wordmark.
Badly-set type destroys the effect. Doesn't demo anything. Hard to maintain
because the headline and the illustration both have to land.

**Cryo fit: STRONG.** IBM Plex Sans SemiBold at 56-64 px on Snow with one
small Slate eyebrow line and the Core on the right per brand §10 is *already
this archetype*. Don't overthink it. The risk is over-decorating — the moment
we add a "live counter" or a "stats band" or a "floating card" we're back in
generic-SaaS territory. **Recommendation: type-first hero, Core on the right,
Core-blue button + text link, period.**

### 1d. "Live-data hero" (real numbers animating)

- **Tigris** — https://tigrisdata.com — hero shows a real request-volume chart
  animating against time. Numbers in monospace.
- **ClickHouse** — https://clickhouse.com — "rows per second" chart animating
  on load. Yellow brand accent.
- **PlanetScale (legacy)** — https://planetscale.com — query-throughput chart
  embedded. (Pivot since; the pattern persists.)

**Why it works:** if the numbers are real, this is the strongest "we do the
thing" signal in the entire pattern library. If the numbers are mocked, it's
the worst.

**Why it fails:** if the numbers are made-up, the brand loses trust forever
once anyone catches it. If the numbers are from production, they may be
confidential. If the numbers are from a public source, they age (the day
someone checks and the chart's stale, the whole page is suspect).

**Cryo fit: CAUTION.** Brand guide §11 explicitly forbids invented performance
numbers on marketing surfaces. A live "compaction rate" or "files compacted
across all Cryo deployments" chart would either (a) be fake (banned),
(b) leak production data (banned), or (c) be a single number from one
deployment (becomes stale). **There is one safe variant:** a *visual, not
measured* animation showing the Core's tiles settling into blocks (brand §10's
"one purposeful animation, under 1.2 s, once"). That's the live-data hero,
re-stated in the brand's own idiom. Keep the motion; lose the number.

### 1e. "Split-with-console" (text left, product shot right)

- **Stripe** — https://stripe.com — left: three short value props; right:
  realistic API console with realistic code.
- **Linear (variations)** — same pattern, dark.
- **Supabase** — https://supabase.com — same pattern, emerald-accented.

**Why it works:** the product shot is doing the explaining. The copy is short.

**Why it fails:** if the product shot is a generic dashboard, the page reads
as every other dev-tool site. The differentiator is *which* shot, not
*whether* a shot. Stripe wins because the shot is recognisably Stripe; the
pattern is just the framing.

**Cryo fit: STRONG (with the brand-correct shot).** Brand §10 already locks
"hero: left text, right the Core". The catch: the "console" half of the
pattern wants to be a real Cryo UI screenshot. Brand §10 also bans recolouring
UI screenshots. So if we ship a screenshot, it's the existing cobalt-accent
product UI inside a plain Firn-bordered frame (per §10) — honest and
on-brand. **Recommendation: the homepage hero keeps the Core on the right
(no fake UI), but on the **maintenance page** or **quickstart page**, the
right side becomes a real product screenshot. Two different heroes, two
different jobs.**

---

## 2. Section archetypes

The space *after* the hero matters more than the hero, because that's where
readers decide whether to trust the project.

### 2a. "Read next" / "Steps" pattern

- **Anthropic Claude docs** — https://platform.claude.com/docs — `<Steps>`
  component: numbered, one task per step, one sentence + one link each.
  "Recommended path for new developers."
- **Astro Starlight docs** — https://starlight.astro.build — same idea under
  "Next steps" footer of every page.
- **Vercel docs** — https://vercel.com/docs — same idea, called "Read Next".

**Why it works:** a docs site that ends every page with "go here next" feels
designed for reading, not for selling. The numbered ordering tells the reader
*you don't have to plan*, just go.

**Cryo fit: SHIP IT.** Already partially present (per
`DOCS_SITE_CREATIVE_PLAN_2026-09-30.md` §2). Make it consistent on every page.
Use the brand's existing card styling — don't invent a new component.

### 2b. "Comparison rail" / "Pick the one you need"

- **Claude docs** — Messages API vs Managed Agents table on the homepage.
- **Vercel docs** — Next.js vs SvelteKit vs Nuxt framework matrix.
- **Astro integrations page** — https://astro.build/integrations — tag-filters
  on a grid of integrations.

**Why it works:** answers "is this for me" in 30 seconds. A reader who
arrives unsure leaves sure. Readers who were never unsure skim it and move on
— no harm.

**Cryo fit: SHIP IT.** `DOCS_SITE_CREATIVE_PLAN_2026-09-30.md` §1 already
flags this. Lift the existing reference-page matrix onto the homepage, trimmed
to 3-4 rows (Cryo / DIY DAGs / Amoro / LakeFS).

### 2c. "Live counter band" / "Stats strip"

- **Cal.com homepage** — "X contributors · Y PRs merged · Z stars" all live.
- **Dagger homepage** — GitHub stars counting up.
- **tRPC homepage** — stars + downloads counter.

**Why it works:** signals "this is real and growing". Cheap to implement.

**Why it fails:** if the number is fake or stalled, it ages the site within
weeks. Many counters are visibly broken (showing "1,234" forever).

**Cryo fit: MEDIUM.** GitHub stars and contributors count are safe to surface
— both come from a public API the reader can verify, and they're real OSS
proof. Recommended placement: footer or one tight band below the hero. **Not
in the hero.** The brand guide doesn't ban numbers, it bans *made-up*
numbers; these aren't made up.

### 2d. "Live / inline sandbox"

- **Svelte REPL** — https://svelte.dev/playground — embedded REPL inside every
  docs page.
- **CodeSandbox / StackBlitz embeds** — common on third-party docs.
- **MDN live samples** — https://developer.mozilla.org — every HTML/JS example
  is runnable.

**Why it works:** the reader's mental model collapses from "I have to set
this up to try it" to "I tried it just now." Largest trust multiplier in the
pattern library.

**Why it fails:** requires either a backend (expensive), a static
wasm/transpile pipeline (heavy), or external CDN embeds (privacy + latency +
banned stock-photo-pattern). For backend products like Cryo, "run a
compaction in the docs" is a much bigger ask than "run a Svelte component."

**Cryo fit: NARROW.** Skip the heavyweight sandbox. Do ship a much smaller
equivalent: a **simulated CLI output block** that animates step-by-step on
scroll, with each step a hand-crafted realistic-looking log line. That's the
"Live demo" feel without a backend, and it fits the brand's "calm, exact,
operational" voice perfectly. Less ambitious than Svelte's REPL; honest about
what we are.

### 2e. "Pull quote with provenance"

- **Stripe blog** — blockquote with the cited paper/benchmark attached.
- **NYT/Wired** — pull-quote with the source in small text underneath.
- **Cal.com / Linear changelog** — quoted release notes from real PRs.

**Why it works:** if the quote is real and the source is visible, it reads as
"this team did the reading." If the quote is decorative, it reads as filler.

**Cryo fit: LOW for marketing; HIGH for technical pages.** On the maintenance
concepts page, a pull-quote from a real Iceberg / Amoro / LakeFS blog post
(no logo, no name) with a citation in Slate small-caps-style would land. On
the homepage, skip pull-quotes — they're marketing furniture and Cryo's
brand guide is explicit that the site sells on mechanism, not endorsement.

---

## 3. Diagram systems

Cryo already ships Archify (interactive JSON-IR) and Mermaid (declarative)
diagrams. The question is: what should the multi-source diagram system *do*
together that neither does alone?

### 3a. What the leaders do

- **Stripe** — diagrams are bespoke hand-drawn SVGs, every one in the house
  style. No diagram tool visible to the reader. **Cost: every diagram is art
  direction.** Cryo can't afford that.
- **Temporal** — https://docs.temporal.io — Mermaid + hand-drawn mix. Mermaid
  for the high-churn explainers, hand-drawn for the anchor diagrams. The
  reader can tell which is which; that's fine.
- **Tigris** — https://docs.tigrisdata.com — Archify-class interactive
  diagrams for architecture, static for everything else.
- **dbt Labs** — https://docs.getdbt.com — dbt's DAG is the diagram; every
  page renders the relevant slice. Highly interactive.
- **Datadog** — bespoke diagrams everywhere, but their audience pays for
  that polish.
- **D2** — https://d2lang.com — modern declarative, themeable, popular in
  OSS. (GitHub-style diagram-as-code.)
- **Kroki** — https://kroki.io — gateway that renders Mermaid/D2/PlantUML/
  Graphviz/etc. behind one URL. Useful for CI.
- **Structurizr** — https://structurizr.com — DSL + C4 model, great for
  architecture.
- **Excalidraw** — https://excalidraw.com — hand-drawn, popular for OSS
  architecture pages where the visual signal is "this is humans talking".

### 3b. Recommendations

1. **Two-track system, by job.** Archify for the diagrams where the reader
   needs to *explore* (architecture, fleet topology). Mermaid for the
   diagrams where the reader needs to *understand* (flows, decision trees,
   op order from `planner/guardrails.py`). Don't mix tracks in one diagram.
2. **Mermaid is themable.** Lock the theme block from brand §8 in a shared
   snippet. Every contributor's diagram inherits it.
3. **Archify for explorers.** The existing Archify embed pattern (iframe,
   `?embed=1&theme=light`) is correct. Treat each interactive diagram as a
   *navigable page in its own right*: deep-linkable, presenter-mode-enabled,
   screenshot-able. The architecture page is the obvious first user; the
   page that explains `CORRECT_ORDER` in detail is the second.
4. **D2 is the dark-horse option.** If Mermaid feels too rigid for a future
   diagram, D2 is a drop-in alternative with better text-on-path and grid
   layouts. Don't adopt until you need it.
5. **Static SVG as the escape hatch.** For one-off concept diagrams where
   the brand grammar (the Core, the marker) has to be perfect, hand-roll a
   static SVG. Use the existing `charts.js` infrastructure for things that
   fit its primitives (`figure` for node-edge, `rail` for numbered).

**Hard rule: never use an AI image model for diagrams.** Brand §8 says so
explicitly, and the reason is correct: labels and arrows must be exact.

---

## 4. Trust and social proof (OSS-appropriate)

The brand guide bans fake testimonials, stock photos, and AI-generated people.
The good news: OSS projects have *better* signals than paid SaaS. Use those.

### 4a. Signals that work for OSS

- **GitHub star count, live from the API** — Dagger, tRPC, Cal.com all do
  this. Honest, sourced, verifiable.
- **Contributor count + contributor grid** — Polar.sh's customer-mosaic,
  retargeted at contributors. Opt-in only.
- **Commit cadence chart** — a small bars chart of commits/week from the
  public repo. Reads as "this project is alive."
- **"Shipped in production at" callouts** — single line, no logo, optional
  company-name link. Format: *"Shipped at <Company> since 2024-Q3."* No
  quotes, no testimonials, just presence. **Forbidden in Cryo's brand if
  it's a named customer without explicit permission**, per brand §2.
- **Real screenshots from issue threads** — a Slack/GitHub issue with a real
  user's report + the fix in the next comment. Anonymise names; keep the
  substance.
- **"Reviewed by" or "used by" with public-org names only** — if a public
  project mentions Cryo in their CI / docs, that's public signal. Cite the
  URL.
- **Citation badges for cited papers** — AutoComp, WAIR, PTO (already
  referenced in `DOCS_SITE_CREATIVE_PLAN_2026-09-30.md` §5) deserve small
  "[paper]" badges that link to the source. Not a vanity claim; a real
  pointer.

### 4b. Signals to skip

- **Vendor logo wall without permission** — looks needy, ages badly, and
  risks the brand §2 ban on competitor logos.
- **"Trusted by 10,000 teams"** — meaningless unless verifiable.
- **Stock photos of engineers at desks** — banned.
- **Capterra/G2-style rating badges** — reads as marketing.
- **"Featured in TechCrunch"** — unless the article is actually about the
  project's mechanism, not its funding.

### 4c. Cryo recommendation

Three on-brand trust elements, in priority order:

1. **Footer band: GitHub stars + contributor count + latest release tag, all
   live.** Three numbers, monospace, no decoration. Each is one API call.
2. **A single "Used by" line per page where relevant**, citing public
   mentions only (GitHub issues, blog posts, conference talks). One line,
   no logo, link to source.
3. **A real-receipt section** on the maintenance page showing actual
   planner output (anonymised), with the "Illustrative · DATE" caption per
   the oss-docs-site skill's "illustrative, not measured" rule.

That's the whole trust surface. Anything more is decoration.

---

## 5. Interactive demos (without a real backend)

The hard constraint: Cryo is a backend platform. No shipping backend, no real
demo. But there's a real spectrum between "static screenshot" and "live API".

### 5a. Realistic demo archetypes for backend dev tools

| Archetype | Example | Effort | Honesty |
|---|---|---|---|
| Static product screenshot in a frame | Most OSS sites | trivial | high |
| Static screenshot with hover annotations | Stripe docs | low | high |
| Pre-rendered animated terminal output | Stripe, Vercel | medium | high |
| Click-to-cycle screenshots of a flow | Cal.com pricing pages | low | medium |
| In-browser shell terminal (pre-scripted) | https://lab.terminaltrove.com | medium | medium |
| Live API playground (real backend) | Stripe, Twilio | huge | highest |
| WebAssembly port of the real thing | DuckDB-WASM, SQLite-WASM | huge | highest |
| Slider-to-show-file-count demo | (no canonical example) | medium | high |

### 5b. Cryo recommendations, in priority order

1. **Pre-rendered animated terminal** — *the obvious win.* Hand-craft a
   30-second realistic-looking CLI session: `cryo scan`, `cryo run`, `cryo
   receipts show <id>`. Each line fades in, the next appears, the prompt
   blinks. Recorded once, played on hover/intersection. Fits the brand's
   "operational" voice. Avoids any backend.
2. **Click-to-cycle flow** — a 3-4 frame storyboard of a table going from
   "many small files" → "compacted" → "expired snapshots" → "clean state",
   with one marker on the active frame. Could be a hand-rolled SVG or
   four screenshots.
3. **"Drag the slider, see file count change"** — the small-files problem
   visualised: slider moves from 1,000 files to 10 files, the Core
   visually compacts in step. Beautiful, on-brand (the Core *is* this
   image), no backend. The single most brand-correct interactive demo in
   the brief.
4. **In-browser fake shell** — Xterm.js with a pre-scripted JSON session
   that responds to a small number of typed commands. Useful for the
   quickstart page; risks feeling toy-ish.

Skip: live API playground (no backend), WebAssembly port (huge investment,
not in scope for a docs-site iteration), slider-to-change-the-product
(misleading).

---

## 6. On-this-page navigation

Cryo already ships a right-rail scrollspy. The 2025-2026 state of the art:

### 6a. Patterns observed

- **Right-rail scrollspy (the dominant pattern)** — Stripe, Vercel, MDN,
  Astro Starlight, Linear docs. Active heading highlighted, parent heading
  open, scroll progress often shown as a thin line.
- **Top progress bar** — MDN, GitHub blog, long-form articles. Subtle.
- **Breadcrumb-with-jump** — Docusaurus, GitBook. The breadcrumb becomes a
  section picker.
- **Hybrid: scrollspy + top progress bar** — Astro Starlight does this
  well. Right rail tracks heading, top edge of rail is a thin progress fill.
- **No TOC at all** — Bun, Deno docs. Works for short reference pages,
  fails on long explanatory pages.

### 6b. What works for long-form technical content

Right rail wins. Top progress bar is a quiet companion. Breadcrumbs are a
*different* navigation aid (page ancestry) — not a substitute for scrollspy.

### 6c. Cryo recommendations

Keep the right-rail scrollspy. Two small upgrades:

1. **Add the top-edge progress fill** to the rail itself (5 px wide, Firn
   background, Core-blue fill from top to current position). Single line of
   CSS + one observer. The oss-docs-site skill's `progress-rail` chart
   primitive already exists; reuse it.
2. **Mark the active H3 too**, not just H2. Long Cryo pages (architecture,
   maintenance) have meaningful H3s that the reader scrolls to. Most
   scrollspy libraries stop at H2 by default; push it one level.

Skip: full-screen TOC overlay (works on phones, breaks on desktop), a
bread-crumb section picker (duplicates information the left rail already
shows).

---

## 7. Search

The current Cryo site uses a build-time JSON index plus ~80 lines of runtime
code. That's a reasonable baseline. What's the state of the art in 2026?

### 7a. The serious options

- **Pagefind** — https://pagefind.app — fully static, build-time index,
  excellent out-of-the-box UI (chunked results, excerpts, keyboard nav,
  dark mode). What the OSS standard has converged on for static sites in
  2025-2026. Confirm: pagefind.app renders Hugo itself.
- **Algolia DocSearch** — https://docsearch.algolia.com — free for OSS,
  best-in-class UX (instant, autocomplete, ranked). Cost: an external
  dependency, a JS bundle from a CDN, and a small review queue for OSS
  eligibility. Still the *premium* option.
- **Orama Search** — https://oramasearch.com — note the domain was
  acquired by another company in 2025 ("Metis · The Enterprise Intelligence
  OS" now serves there); the project's active home is on GitHub
  (oramasearch/orama). Modern in-memory JS engine, supports typo tolerance
  and vector search. Competitive with Pagefind for JS-only sites; heavier.
- **MiniSearch** — https://lucaong.github.io/minisearch — lightweight,
  in-memory fuzzy. Good if you want to keep everything in 80 lines of code
  and don't need Pagefind's UI.
- **Stork** — https://stork-search.net — Rust-built WASM index, fast,
  smaller bundles. Niche but well-loved.
- **FlexSearch** — https://github.com/nextapps-de/flexsearch — fastest JS
  search, lower-level. Good if you want to write the UI yourself.

### 7b. Recommendation for Cryo

**Move to Pagefind** in the next iteration. Concrete reasons:

1. It's static (matches Cryo's no-build philosophy per the oss-docs-site
   skill §9).
2. The default UI is already good and themeable to the brand palette.
3. Pagefind handles chunked excerpts, keyboard nav, and dark-mode for
   free — all three are things the current hand-rolled index does poorly.
4. It's the *common* choice now; familiar to OSS contributors who'll
   expect it.
5. The current hand-rolled JSON index can co-exist during migration — both
   can ship behind a feature flag.

Skip Algolia unless the team wants a managed service and is willing to give
up some autonomy. Skip Orama (the project's domain instability is a yellow
flag, not a deal-breaker, but Pagefind is the safer pick for a docs site).

---

## 8. Motion philosophy

The brand guide already answers most of this ("at most one purposeful
animation, under 1.2 s, respect `prefers-reduced-motion`"). The question is
what "purposeful" looks like.

### 8a. The leaders, by posture

- **Linear** — subtle. Heading underline animates; product shot drifts.
  Never ambient.
- **Vercel** — restrained. Logo hover-lift; gradient mesh on some subpages.
- **Cal.com** — playful. Calendar tile-flip on hover. Confident on the
  homepage, lighter in docs.
- **Sentry** — utilitarian. Almost no motion outside dashboards.
- **Tigris** — restrained. Live charts animate; that's it.
- **Inngest** — confident. The hero is one continuous animation. The rest
  of the site is still.

### 8b. Cryo posture: "calm, exact, operational"

Translation: motion that **confirms** what the reader already sees. Not
motion that performs for its own sake.

### 8c. Recommendations

1. **Hero tiles-settling animation** (brand §10, already endorsed) — Core
   tiles settle from loose to solid once on load, under 1.2 s, exactly
   once. Reads as "compaction happened." This is the *one* site-wide
   animation.
2. **No scroll-triggered animations.** They age badly, fail on phones,
   and compete with the reader's intent. The exceptions: chart re-veal on
   intersection (already implemented) and the progress-rail fill.
3. **No hover-lift on cards.** Cards are reference, not toys. A 1 px
   border-darken on hover is plenty.
4. **No page transitions.** Instant page loads match the brand's
   operational voice. A 200 ms fade-out + fade-in reads as theatre.
5. **Chart entry: yes, ambient pulse: no.** When a chart enters the
   viewport, a 400 ms number-count is appropriate. After that, static.
6. **`prefers-reduced-motion` must be wired everywhere** — not just the
   hero. Test with the OS setting on.

---

## 9. Mobile patterns for long-form docs

Most long-form docs sites in 2025-2026 do the same five things on mobile,
with small variations.

### 9a. What collapses well

- **Left rail nav → bottom sheet or hamburger** — every site does this.
  Cal.com and Linear do it well.
- **Right rail scrollspy → floating button** that opens a bottom-sheet
  TOC (Astro Starlight, Vercel).
- **Multi-column hero → stacked** — text first, illustration below.
- **Inline architecture diagrams → full-width with horizontal scroll, or
  pinch-to-zoom (iframe gets `scrolling="no"` removed, or wrapped in a
  `transform: scale(...)` shell)** — this is the pattern that breaks most
  often; test it.
- **Customer-mosaic → vertical stack of one tile per row** — Polar does this.
- **Code blocks → full-width with horizontal scroll for long lines**.

### 9b. What stays

- **Search bar** — top of every page, accessible via a single tap.
- **Code blocks** — never wrap, always horizontal scroll.
- **Page-type indicator** — the small "Tutorial / How-to / Reference /
  Explanation" tag from the oss-docs-site skill is even more useful on
  mobile (no big sidebar to read it from).
- **Section anchors** — every H2/H3 needs a stable `#id` and a hover-
  visible link icon, otherwise readers can't share a position on mobile.

### 9c. Cryo recommendations

- Collapse left rail to a top-bar hamburger.
- Move right-rail TOC behind a floating "On this page" button at the
  bottom-right of the viewport, opens a bottom-sheet.
- Stacked hero: text first, Core illustration below.
- Test Archify iframes on real mobile (iPhone Safari + Android Chrome) —
  touch-zoom is the most common failure mode for embedded diagrams.
- Don't hide the Core illustration on mobile — it's the brand asset. Just
  shrink it.

---

## 10. What to AVOID (2026-specific)

These patterns either date the site to 2023 or break the brand guide
explicitly.

### 10a. Patterns that look dated in 2026

- **3D product mockups floating in space** — was hot in 2022-23, now
  reads as "designer got bored."
- **Gradient mesh hero backgrounds** — Vercel-origin, then everyone,
  now exhausted. Banned by Cryo brand anyway.
- **Neumorphism** — never recovered from its 2020 stumble.
- **Glassmorphism panels** — Vercel-origin, Apple-spinner. Banned.
- **Marquee customer-logo strips** — "as seen in" energy. Looks needy.
  Banned by brand §2 spirit (no logo walls).
- **Heavy parallax** — every long-form site killed parallax by 2024.
  Itch from "we couldn't afford video".
- **Auto-rotating carousels** — accessibility-disaster. Replace with a
  static grid.
- **Sticky "Subscribe to our newsletter" popups** — banned by every
  dev-tool site that grew up.
- **AI-sparkle icons next to features** — the "we added AI" cliché.
  Banned by brand §2 (no robots, no AI sparkle).
- **Hexagonal logo grids** — the "cloud native" cliché. Banned by the
  fact that no engineer trusts them.
- **Hover-to-reveal screenshots** — fights accessibility, fights
  keyboard users, ages the moment the screenshot changes.

### 10b. Patterns that would clash with Cryo's brand specifically

- **Any gradient** — banned (brand §2).
- **Any purple, indigo, or violet** — banned (brand §2). Linear and
  Supabase earn this colour; Cryo can't.
- **People, faces, hands** — banned (brand §2).
- **Iceberg imagery** — banned (brand §2; this is the most important
  ban in the whole guide for an Iceberg-vendor).
- **Snowflakes, penguins, polar bears** — banned (brand §2).
- **AI-generated code screenshots with fake error messages** —
  dishonest, banned (brand §2 "fake interfaces inside generated images").
- **Glass / frosted panels** — banned.
- **Large orange fills** — Survey orange is the marker, never text,
  never large areas (brand §4 proportions rule).
- **Firn or Glacier as text colour on light backgrounds** — fails
  contrast (brand §4 contrast table).
- **Cobalt-accent product screenshots** — only acceptable *as-is*;
  never recolour (brand §10).
- **Words / numbers inside AI-generated illustrations** — banned
  (brand §2).

### 10c. Patterns that *seem* safe but aren't

- **Generic dashboards with placeholder graphs** — read as every other
  dev-tool site. The single biggest visual trap.
- **"We'll add a dark mode toggle later"** — has to ship at the same
  time as light mode or the CSS variables won't compile.
- **A "play with the API" CTA that goes to a 404** — kills trust. Every
  CTA must resolve.
- **Empty state illustrations of sad clouds** — overdone. Empty state
  is a chance to use the Core motif (a Core with no segments on the rack),
  not to add a new illustration system.

---

## Appendix A — The single highest-leverage next iteration, distilled

If the team does exactly one thing this iteration, it should be:

> **Move the right-rail scrollspy into a dual-mode surface (desktop rail
> + mobile bottom-sheet "On this page" button), upgrade the search to
> Pagefind, and lock the homepage hero as type-first with the Core on
> the right. Skip every other category until those three are right.**

That's the smallest set of changes that addresses the most visible
problems, all of them without introducing new colours, new motion, or
new illustration systems.

---

## Appendix B — Site-name-to-URL index

For the designer's tab bar:

| Site | URL | Why |
|---|---|---|
| Anthropic Claude docs | https://platform.claude.com/docs | Steps / Cards / comparison table |
| Astro Starlight docs | https://starlight.astro.build | TOC + progress rail + search |
| Cal.com | https://cal.com | Interactive hero |
| ClickHouse | https://clickhouse.com | Live-data hero |
| D2 | https://d2lang.com | Diagram-as-code, themeable |
| Dagger | https://dagger.io | Pipeline hero + OSS trust |
| Docusaurus showcase | https://docusaurus.io/showcase | Catalog of OSS docs |
| DuckDB docs | https://duckdb.org/docs/current | Code-first, fast, calm |
| Excalidraw | https://excalidraw.com | Hand-drawn diagrams |
| Framer | https://framer.com | Type + motion |
| Hono docs | https://hono.dev/docs | Code-as-hero |
| Inngest | https://www.inngest.com | Animated flow hero |
| Kroki | https://kroki.io | Diagram gateway |
| lakeFS docs | https://docs.lakefs.io | Reference-style competitor |
| Linear | https://linear.app | Type-first hero |
| Mintlify | https://mintlify.com | Framework used by Claude |
| Mermaid | https://mermaid.js.org | Declarative diagrams |
| Orama Search | https://github.com/oramasearch/orama | In-memory search (domain acquired) |
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

*Brief prepared as research only. No code, no plan to implement, no
recommendation to install any tooling. Designed to inform the
designer/engineer scoping the next iteration of the Cryo docs site.*