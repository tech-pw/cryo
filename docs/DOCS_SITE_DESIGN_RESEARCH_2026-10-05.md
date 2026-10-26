# Docs site design research and redesign spec (2026-10-05)

Scope: `site/` (the public GitHub Pages docs). This replaces the visual and navigation layer; the
information on the pages is kept. This document supersedes the earlier creative plan where they
disagree, because these numbers were measured on live sites.

Working state for anyone continuing the redesign:
[`DOCS_SITE_REDESIGN_HANDOFF_2026-10-05.md`](./DOCS_SITE_REDESIGN_HANDOFF_2026-10-05.md).

## 1. Method

- Drove headless Chrome (1440 x 900, light and dark) against **20 documentation sites**: Stripe,
  Vercel, Next.js, Tailwind CSS, Supabase, Cloudflare, Linear, ClickHouse, DuckDB, Trino, Apache
  Iceberg, dbt, Grafana, Temporal, Mintlify, Fumadocs, Astro Starlight, Docusaurus, Material for
  MkDocs, Vite. Took 78 screenshots and read computed styles (colour, type, widths, radii).
- Separately captured the **architecture-diagram sections** of Supabase, TiDB, Docker, Kubernetes,
  Temporal, Dagster, Cloudflare and CockroachDB docs.
- Screenshotted our own site at the same size and read it against the same yardsticks.
- Read the [Diátaxis](https://diataxis.fr/) documentation framework (Cloudflare, Gatsby and Vonage
  cite it as the basis for their docs information architecture).
- Feature-presence counts below come from text/DOM heuristics, so treat them as "most" and "few",
  not exact.

Scripts are throwaway (`/tmp`); the numbers that matter are recorded here.

## 2. What the measurements say

### Shell and measure

| Property | Observed range | Typical | Cryo before |
|---|---|---|---|
| Header height | 48-64 px | 57-64 px | 68 px |
| Left nav width | 207-325 px | 280-300 px | 256 px |
| Reading column | 648-884 px | 700-800 px | 1180 px (two-up cards) |
| Right "on this page" rail | 224-304 px | about 240 px | 220 px |
| Body type | 16 px / 24-28 px line | 16 px | 17 px |
| Page `h1` (content pages) | 28-42 px, weight 500-600, tracking -0.02 to -0.05em | 32-36 px | 64-84 px |
| Code block radius | 6-10 px | 8 px | 10 px |
| Page background in dark | `#08090a`-`#1e2129`, never pure black on most | near-black navy or grey | `#0b0f16` (fine) |

Takeaway: every well-regarded docs site keeps a **three-column shell with a narrow reading
column**. Cryo's 1180 px content area with side-by-side cards is the biggest outlier, and its 84 px
home headline is the second. Marketing-scale type belongs on a landing page, not on a reference
page.

### Colour

Every site uses a **neutral surface plus exactly one accent**, applied to links, the primary
button and the active nav item. Nothing else is coloured.

| Site | Neutral (light / dark) | Accent |
|---|---|---|
| Vercel, Next.js | `#fafafa`/`#fff` and `#000` | black; link blue `#0072f5` / `#52a8ff` |
| Stripe | cool grey-white | indigo `#5469d4` |
| Starlight | `#fff` / `#17181c` | indigo `#3d50f5` / `#b3c7ff` |
| Docusaurus | `#fff` / `#1b1b1d` | green (default theme) |
| Supabase | near-white / dark green-grey | green |
| Cloudflare | near-white / `#2f2f2f` | orange (one button, active item) |
| Mintlify | white / black | green `#166534` / `#86efac` |
| Linear | `#08090a` | monochrome |
| Fumadocs | `#f5f5f5` / `#121212` | monochrome |

Dark mode swaps to a **lighter tint of the same accent** (Next `#0072f5` to `#52a8ff`, Starlight
`#3d50f5` to `#b3c7ff`, Mintlify `#166534` to `#86efac`). It does not reuse the light-mode value.
Cryo's brand tokens already behave this way (`#266fa8` to `#90d4f0`), which is a good sign.

### Wayfinding features, by how common they are

- **Search with a keyboard shortcut** (`⌘K` or `/`): on most sites. It lives in the header, not the
  sidebar. Results are page and heading level, not page level.
- **"On this page" rail with scrollspy**: nearly universal, usually h2 and h3.
- **Code blocks with a copy button**: nearly universal; dark blocks even in light theme (Tailwind,
  Next, Stripe).
- **Prev / next at the foot of the page**: Starlight, Docusaurus, Mintlify, Temporal, ClickHouse.
- **Breadcrumbs** above the title: Linear, ClickHouse, Mintlify (as a small section label).
- **Edit this page / feedback**: common; Kubernetes, Docker and Next put "Edit on GitHub" in the
  right rail.
- **"Copy page" / "View as Markdown" / "Ask AI"**: about half of the sites, and nearly all the
  newer ones (Vercel, Linear, Mintlify, ClickHouse, Fumadocs, Supabase). It is the clearest
  2025-26 shift.
- **Callouts with an icon and a tinted background** (Starlight, Mintlify, ClickHouse), not a
  coloured left stripe.
- **Numbered step lists** (ClickHouse, Stripe, Starlight) for any procedure.
- **Tabs that stay in sync** (npm/pnpm/yarn, language pickers) so a reader chooses once.

### Landing pages

- **Stripe / Supabase / DuckDB**: the docs home is a *task index*, not a pitch: short links grouped
  by what the reader wants to do ("Accept payments online", "Connect a framework").
- **Cloudflare**: one-line pitch, one primary action, then a tabbed "primitives" demo with a
  copyable command.
- **Iceberg itself** uses an illustrated hero (penguins and icebergs). The Cryo brand guide
  deliberately forbids that vocabulary, which is useful: Cryo's identity is the *core sample*, not
  the iceberg.
- A page that opens with a short pitch, one install command and one real diagram beats a hero with
  decorative metrics. DESIGN.md already bans hero metrics and fake dashboards.

### Information architecture

[Diátaxis](https://diataxis.fr/) separates docs by reader need: **learn** (tutorial), **do**
(how-to), **look up** (reference), **understand** (explanation). Cloudflare used it to rebuild its
developer docs. Cryo's nine pages already split cleanly along these lines; the nav simply does not
say so. Fix by grouping and labelling, not by rewriting content.

## 3. Architecture diagrams: what the good ones do

| Source | What works | What does not |
|---|---|---|
| **Supabase** | Native, theme-aware SVG. One gateway bar over a row of services over the database. Edges carry a small route label (`/auth`, `/rest`). About 10 nodes. | Monochrome; relies on prose for roles. |
| **TiDB** | Dashed group boxes with a cluster label; three role colours; two-headed arrows labelled with the protocol. | Raster image; it does not follow the theme. |
| **Docker** | Three swimlanes (Client / Host / Registry) with the *verb* on each arrow (`docker run`, `build`, `pull`). | Raster; fine detail unreadable on mobile. |
| **Kubernetes** | Thorough, uses a "Figure 1" caption and an expandable "About this architecture" note. | White raster on a dark page. |
| **Temporal** | A single clear idea per diagram. | White raster in dark mode again. |

Rules distilled for Cryo:

1. **Native SVG, theme-aware.** Rasters and iframes fail in dark mode (three of the five above).
2. **One idea per diagram, at most ~12 nodes.** If it needs more, split it.
3. **Group with labelled lanes**, not with colour alone.
4. **Name every edge** with a verb or protocol, and show direction.
5. **Read left to right / top to bottom**, with the read side and the write side kept visibly apart.
6. **Number figures, caption them, and give a text alternative** (expandable). Needed for
   accessibility and for search.
7. **Let nodes be links** to the page section that explains them. This turns a diagram into
   navigation.
8. **Allow zoom** (expand to a larger view) rather than shrinking text on small screens.
9. **Exactly one emphasis marker per diagram.** The Cryo brand guide already specifies this: a
   single survey-orange flag on the thing that matters.

## 4. What was wrong with the previous Cryo site

Evidence is the 1440 px screenshots taken today, not opinion.

1. **Hero collisions.** The home hero card is a bright panel inside a dark page, its stat cells
   overprint their own labels, and the headline wraps to five lines at 84 px.
2. **Diagrams are unreadable.** The in-page "figure" charts render at roughly 40% of a legible
   size; the real architecture sits inside 750 KB iframes that reload when the theme changes.
3. **Invented numbers.** "87 table grade", "9.2k files / run", an "API surface area" chart with
   made-up endpoint counts, fleet-grade donuts and a bytes-reclaimed waterfall. They are labelled
   illustrative, but a reference site that shows fake data trains readers to distrust the real
   data. This also conflicts with DESIGN.md's ban on hero metrics.
4. **One factual error.** The health page's worked example subtracts 8 + 8 + 8 + 20 from 100 and
   calls the result 64. It is 56, a D. The fix is two warnings plus one critical (64, a C).
5. **Card-in-card density.** Each page is a stack of bordered boxes, then four "Read next" cards.
   There is no prose rhythm; sections do not read as documents.
6. **Brand-rule drift.** Uppercase tracked mono eyebrows (brand guide section 5 and DESIGN.md ban
   them), the survey-orange used for text and the logo dot (brand section 4: marker shape only),
   and Google Fonts despite the product bundling fonts locally.
7. **Chrome is injected by JavaScript**, so there is no nav or footer without JS and the page jumps
   on load.
8. **Search is a sidebar filter** that returns whole pages only.

## 5. Redesign decisions

Brand is pinned (colour, IBM Plex, flat vector "core sample" look, no gradients/glow/glass). The
redesign changes structure, density and components, not the identity.

**Shell.** 56 px header (logo, section tabs, search with `⌘K`, GitHub, theme). 272 px left nav in
plain sentence-case groups, 232 px right "on this page" rail with scrollspy (h2 + h3), reading
column about 760 px with figures allowed to use the full article width. Under 1180 px the rail
collapses into a disclosure; under 900 px the nav becomes a drawer.

**Per-page chrome.** Breadcrumb, `h1` at 36 px, lede, then a quiet meta row: updated date, edit on
GitHub, copy page (as Markdown). Foot: prev / next, replacing the four-card "Read next" grids.

**Colour.** Restrained strategy: Snow/White/Night surfaces, Deep-ice text, Core-blue as the only
accent (links use `#266fa8`, selected text `#1f5f94`, per DESIGN.md contrast rules). Survey-orange
appears **only** as the single marker flag on each diagram. Code blocks use the Deep-ice canvas in
both themes.

**Type.** IBM Plex Sans 16/26 body, Plex Mono 13.5 px for code and operation names, self-hosted
woff2. Sentence case everywhere. No uppercase eyebrows.

**Components.** Callouts (note / tip / caution / important) with an icon and tint, code blocks with
title and copy, synced tabs (Trino | Spark), numbered steps, link cards used sparingly, definition
tables, grade pills with letter + label, figure with caption + expand + text alternative.

**Diagrams.** All redrawn as build-time SVG from the existing Archify sources: light cards with a
role icon tile on a dot-grid canvas, tinted lanes for the read side and write side, thin labelled
edges, one orange marker, nodes linked to their docs section (grammar in section 10). The Archify HTML viewers stay in `diagrams/` as optional
route-tracing deep dives; they are no longer embedded.

**Home.** A short pitch, one copyable install command, and the real system map as the first-viewport
proof. Then a "choose your path" index (learn / understand / operate / look up) and the fixed
maintenance order. No fake metrics.

**Search.** `⌘K` / `/` dialog, heading-level index with section snippets, keyboard navigable.

**Build.** A zero-dependency Node generator (`site/scripts/build.mjs`) renders pages from
`site/_src/` into static HTML (nav, footer, TOC, prev/next, diagrams, search index, sitemap). The
output stays committed so the site works with either Pages deployment mode; CI rebuilds before
publishing.

## 6. Deliberately out of scope

- Versioned docs and i18n (single pre-1.0 version, English).
- A feedback widget that needs a backend (we link to GitHub issues instead).
- Replacing the Archify viewers or their JSON sources.
- Any new product claims. Content is unchanged except the corrections listed in section 4.

## 7. Content removed or corrected

| Item | Action |
|---|---|
| Hero stat cells (87, 9.2k, 100%) | Removed; invented. |
| "API surface area (illustrative endpoints)" counts | Removed; route groups listed without fake counts. |
| Fleet-grade donut, bytes-reclaimed waterfall, "Where the bytes live" donut, scanner-read donut | Removed; invented proportions. Byte classes and grade bands are listed as text. |
| Health worked example | Corrected from 56 shown as 64 to a valid 64. |
| Component "responsibility share" treemap | Removed; explicitly not a measurement. |

## 8. Sources

Live sites sampled on 2026-10-05: docs.stripe.com, vercel.com/docs, nextjs.org/docs,
tailwindcss.com/docs, supabase.com/docs, developers.cloudflare.com, linear.app/docs,
clickhouse.com/docs, duckdb.org/docs, trino.io/docs, iceberg.apache.org, docs.getdbt.com,
grafana.com/docs, docs.temporal.io, mintlify.com/docs, fumadocs.dev, starlight.astro.build,
docusaurus.io, squidfunk.github.io/mkdocs-material, vite.dev, docs.docker.com,
kubernetes.io/docs, docs.pingcap.com, docs.dagster.io. Framework: [diataxis.fr](https://diataxis.fr/).
Internal: `docs/brand/CRYO_BRAND_GUIDE.md`, `DESIGN.md`, `PRODUCT.md`.

## 9. Implementation status (2026-10-05)

Shipped in `site/`:

- Generator, fragments and tokens: `site/scripts/build.mjs`, `site/scripts/diagrams.mjs`,
  `site/scripts/highlight.mjs`, `site/_src/` (see `site/README.md` for the workflow).
- Self-hosted IBM Plex under `site/assets/fonts/` (OFL licence alongside). No Google Fonts request.
- Light and dark themes, search dialog, synced tabs, copy page as Markdown, figure Expand dialog,
  hover and focus highlighting on diagrams, mobile drawer, scrollspy TOC.
- Removed: `assets/js/charts.js`, `assets/js/index.json`, `scripts/build-search-index.mjs`.
- CI: `.github/workflows/docs-pages.yml` runs `node site/scripts/build.mjs --check`, then publishes
  a staged tree without `_src/` and `scripts/`.

Known limits:

- Diagrams have a minimum rendered width (about 72 percent of their viewBox). Below that they pan
  inside their frame, and Expand opens them wider. Phones get the text description as the
  primary reading path.
- The 404 page uses absolute URLs so it works at any depth, which means it only renders correctly
  once deployed to the real Pages origin.
- `assets/img/og.png` is a static render. Regenerate it if the tagline changes.

Light-mode palette, adjusted the same day after review: body ink is the product near-black
`#131b24`, not deep ice, because deep ice on snow reads as a blue wash. Cryo nodes use `#1f5f94`
(white labels at 6.7:1; white on core `#277ABE` is only 4.55:1 and fails the 12px subtitles).
Lane borders are slate, and the three lanes use white, frost and a gray tint so a node no longer
sits on a fill of the same colour. Dark mode is unchanged.

## 10. Diagram refresh (2026-10-05, later the same day)

The first build-time SVG pass carried all the information but looked heavy: solid blue boxes,
2–2.5px strokes, dashed lane borders everywhere, boxed edge labels, and viewBoxes up to 1200 wide
that shrank 15px text to about 11px in an 820px article. Several labels also collided with lines
or lane borders.

References compared (screenshots taken live): PlanetScale's dashboard topology, Cloudflare
Reference Architecture diagrams, Stripe Connect's fund-flow diagrams, Neon's architecture page,
Temporal docs, plus Convex, Encore, Inngest, MotherDuck, Railway, Supabase, Tigris, Upstash and
WarpStream. The best ones agree on a few things:

- **Light cards on a quiet dot-grid canvas** (PlanetScale, Stripe, Cloudflare). White card, 1px
  border, a flat 2px drop, 10px radius. The role is carried by a small **coloured icon tile**,
  not by filling the whole card.
- **Thin connectors** (1–1.5px) with a small port dot where they leave a card (PlanetScale), and a
  small arrowhead.
- **Labels sit on the line** with a halo in the background colour, not in a bordered pill
  (Neon, PlanetScale).
- **Groups are tinted panels with a quiet label**, often "Name (qualifier)" with the qualifier
  muted (Neon "Compute node (ephemeral)").
- **Numbered step badges** on sequence messages (Cloudflare).
- **An explicit "not used" mark** beats leaving an edge out.

The Cryo grammar now used by `site/scripts/diagrams.mjs`:

| Element | Rule |
|---|---|
| Canvas | `--d-canvas` with a 16px dot grid (`--d-dot`), in the figure, Expand dialog and home window. |
| Card | 56 high, white (`--d-node`), 1px `--d-node-line`, flat shadow. Cryo cards get a pale blue tint and border. |
| Icon tile | 26px, radius 7, Lucide-geometry glyph. Cryo = core blue, source = frost, engine = deep ice, store = gray, UI = outlined, state = ok / warn / bad. |
| Text | Label 14/600 ink, subtitle 12/400 muted. `code:` switches either to Plex Mono. |
| Edge | 1.5px. Read = blue, write = slate, control = gray dashed, "not read" = dotted with a ✕ mark. |
| Edge label | 12/500 on the line with a 5px halo matching the lane fill under it. |
| Lane | Tinted fill, 1px solid border of the same hue, radius 14, label 12.5/600 with a muted qualifier. `kind: "title"` gives a column heading with no box. |
| Sequence | Stacked heads with icon tile, dotted lifelines, banded phases, numbered step badges. |
| Marker | One survey-orange flag per diagram, 14px pole on the card's top edge. |
| Size | Designed 820–1056 units wide so it renders close to 1:1; the build warns when a label is likely to overflow its card. |

A second pass the same day targeted "understand it at a glance". The visual grammar was fine; what
was missing was a starting point and a point. Three rules, applied to every diagram except the
`platform` map:

1. **Headline the takeaway.** The figure title is the sentence the reader should leave with ("A scan
   reads catalog metadata, never your data files"), shown in ink at 15/600. This is the
   assertion-evidence pattern: the picture confirms a claim instead of asking the reader to find one.
2. **Number the main path.** Badges sit on the line (as on Cloudflare's reference diagrams), and any
   unnumbered edge in a numbered diagram is drawn at 45% as a side path. The eye gets one route.
3. **Say what each part does.** Subtitles moved from implementation ("PyIceberg", "FastAPI") to
   role ("reads metadata", "serves the Web UI"). Branch cards say when they apply ("when one
   partition is bigger than the budget").

This replaces the brand guide's solid Core-blue Cryo boxes inside diagrams. That was a user
decision on 2026-10-05; Cryo components still read as Cryo through the blue tile and tint.

The same pass fixed four design-hook findings in `site.css`: the hero `.grad` and `.stats b` use
solid colour instead of gradient text, the path heading rule animates `transform` instead of
`width`, and the live ops progress line animates `scaleY` instead of `height`.
