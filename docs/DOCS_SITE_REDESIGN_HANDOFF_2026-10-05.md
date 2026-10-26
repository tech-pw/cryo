# Docs site redesign — agent handoff (2026-10-05)

**Read this first** if you are continuing the public docs redesign. The research and the
measurements live in [`DOCS_SITE_DESIGN_RESEARCH_2026-10-05.md`](./DOCS_SITE_DESIGN_RESEARCH_2026-10-05.md).
How to edit a page lives in [`../site/README.md`](../site/README.md). This file is the working state.

Nothing in this work has been committed or deployed. Do not commit, push, or open a pull request
unless the user asks. Do not publish GitHub Pages yourself.

## What the user asked for

1. Redesign `site/` (GitHub Pages). The information was good. The look, feel, and navigation were not.
2. Ground the redesign in how popular documentation sites are actually built: palette, flow, and
   architecture diagrams. That research is done and written up.
3. Make the local preview pick up edits. `make docs-preview` does that.
4. Light mode was harder to read than dark mode. A palette pass landed the same day. The user has
   not signed off on it yet. Look at light mode before changing it again.
5. Leave a handoff so another agent can continue. This file.

## Status

The new site is in the working tree and is what `make docs-preview` serves. Generated HTML is
committed-in-the-tree output (the generator writes it). A preview process may already be listening
on port 4173. If it is, refresh the browser. If it is not:

```bash
make docs-preview          # http://localhost:4173/  — rebuilds on save, Cache-Control: no-store
DOCS_PORT=4321 make docs-preview
make docs                  # one-shot render of site/_src into site/
node site/scripts/build.mjs --check
```

Node 22. No npm dependencies for the site. CI (`.github/workflows/docs-pages.yml`) runs `--check`
and publishes a tree that excludes `site/_src/`, `site/scripts/`, and `site/README.md`. That
workflow has not been run.

Pages and URLs (unchanged slugs):

| URL | Source fragment |
|---|---|
| `/` | `site/_src/pages/index.html` |
| `/quickstart/` | `quickstart.html` |
| `/concepts/` | `concepts.html` |
| `/architecture/` | `architecture.html` |
| `/maintenance/` | `maintenance.html` |
| `/health/` | `health.html` |
| `/engines/` | `engines.html` |
| `/deployment/` | `deployment.html` |
| `/reference/` | `reference.html` |

Nav groups in `site/_src/site.json`: Get started (Overview, Quickstart), Concepts (How Cryo fits,
Architecture, Maintenance), Operate (Health and cost, Engines, Deployment), Reference.

## Decisions that are locked

- Keep the nine pages and their URLs. Do not invent a new information architecture.
- Diátaxis stays in the nav groups: try it, understand it, run it, look it up. The homepage does not repeat that nav. It runs: the hero, the maintenance flow tagged "What Cryo does", the architecture map tagged "How Cryo works" (eyebrow plus one plain sentence, no slogan), the operation order, the four guarantees, then one install line. The map links to the full architecture page. The last section is the contributors block, headed "Built & run by engineers at Physics Wallah." Names come from `site/_src/contributors.json`, a snapshot of the public GitHub contributor list with bots and AI-agent logins removed (`node site/scripts/contributors.mjs`, then `make docs`). Each seat is labelled "Data Platform" and links to the GitHub profile. No logo, no quote, no headcount. Do not publish a profile name that is an email address; set `DISPLAY_NAMES` in `site/scripts/contributors.mjs` instead.
- Three-column docs shell: 56px header, 272px sidebar, reading column about 760–856px, 232px
  right-hand "On this page" with scrollspy. Below 1240px the right rail collapses into a disclosure.
  Below 900px the sidebar is a drawer.
- Sentence case. No uppercase eyebrows, no gradient text, no glow, no glass, no fake metrics.
- Survey orange `#EA580C` is the diagram marker flag only. Never text, never a large fill.
- IBM Plex Sans and Mono, self-hosted under `site/assets/fonts/`. No Google Fonts request.
- Diagrams are build-time SVG from `site/scripts/diagrams.mjs`, themed with CSS variables. They are
  not screenshots and not Mermaid. The Archify HTML viewers in `site/diagrams/` stay as optional
  deep dives (linked from Architecture). Do not embed them again.
- The planner stays engine-agnostic in the prose. Do not put SQL in the planner description.
- No invented numbers. The old site's hero stats, fleet donut, bytes waterfall, treemaps, and fake
  API endpoint counts were removed on purpose. Do not bring them back.
- GitHub project Pages are served at `/Cryo/`. Links in the generated HTML are relative. Do not
  hard-code a leading slash except on `404.html`, which uses absolute URLs so it works at any depth
  and therefore only looks right on the real Pages origin.
- Do not commit unless asked.

## Light-mode palette (current, after the 2026-10-05 review)

Dark mode was already readable and was left alone. Light mode had three problems:

- Body text was deep ice `#143E60` on snow `#F5FAFD`. The ratio is about 10:1, but it is the same
  hue as the page, and macOS antialiasing made it look thin. Body ink is now the product near-black
  `#131B24` (`--primitive-neutral-800` in `web/src/styles/tokens.css`). Ledes use `#3A4554`.
- White on core `#277ABE` is only 4.55:1, so diagram text never sits on core blue. Since the
  diagram refresh below, every card is ink on white (or on a pale blue tint for Cryo), and core
  blue appears only as the icon tile behind a white glyph.
- Lanes, sources, and stores were all frost with firn borders, so regions did not separate. Lanes
  are now distinct tints with a 1px border in the same hue. See the diagram refresh section.

Antialiasing (`-webkit-font-smoothing: antialiased`) applies in dark mode only.

Brand pairs that still hold: snow page, white cards, frost selected nav, slate `#5C667A` for
nav and captions, links `#266FA8` in light and `#90D4F0` in dark, focus `#277ABE`. Firn and glacier
are fills and lines, never text on a light background. Tokens are the two theme blocks at the top
of `site/assets/css/site.css`.

## Diagram refresh (later on 2026-10-05)

The user said the diagrams covered everything but were not crisp or attractive, and asked for a
redesign based on how well-designed companies present theirs. References and rules are in
`DOCS_SITE_DESIGN_RESEARCH_2026-10-05.md` section 10. In short:

- The user picked **light cards with a coloured role icon tile, a dot-grid canvas, and thin
  connectors**. This deliberately replaces the brand guide's solid Core-blue Cryo boxes inside
  diagrams. Cryo cards keep a pale blue tint and a core-blue tile.
- Every diagram was re-laid out at 820–1056 units wide so it renders near 1:1. Label collisions
  with lines and lane borders were fixed, including the overview and system "metadata" labels.
- Edge labels carry a halo matching the lane they sit on, not a bordered pill. "Not read" edges
  (object storage on the scan and projection paths) are dotted with a ✕.
- Content change: the system diagram gained an API → PostgreSQL "reads" edge, because the API
  serves the Web UI from PostgreSQL. The description says so.
- `site.css` gained `--d-*` tokens for canvas, cards, tiles and lanes in both themes. The old
  `--d-cryo-fill`, `--d-cryo-ink` and `--d-lane-line` are gone.
- The impeccable design hook blocks edits to `site.css` while any finding remains, so four were
  fixed with user approval: `.grad` and `.stats b` are solid colour instead of gradient text, the
  path heading rule animates `transform: scaleX`, and the live ops line animates `scaleY` instead
  of `height`.
- New spec fields (`icon`, `tone`, `code`, `lseg`, `lside`, `lpos`, `port`, edge kind `no`, title
  lanes, sequence `xs` / `headW`) are documented in `site/README.md`.
- Follow-up the user asked for ("intuitive, clicks instantly"): figure titles are now takeaway
  sentences, main paths carry numbered `step` badges with side paths muted, and subtitles describe
  roles. Rules are in `site/README.md` under "Make it readable at a glance". The `platform` map
  belongs to a parallel session and was left as it is.

## Content that must stay correct

- Maintenance order, defined only in `cryo/planner/guardrails.py`: `convert_deletes` →
  `rewrite_position_deletes` → `optimize` → `compute_statistics` → `optimize_manifests` →
  `expire_snapshots` → `remove_orphan_files`. Expiry comes after compaction so one run reclaims
  what compaction freed.
- `rewrite_position_deletes` is Spark only. On Trino, `convert_deletes` runs as `OPTIMIZE`.
- Trino `EXECUTE optimize` is Spark `rewrite_data_files`. Trino `optimize_manifests` is Spark
  `rewrite_manifests`.
- Native Go compaction is a configured path, not assumed on.
- Grades: A ≥ 90, B ≥ 75, C ≥ 60, D ≥ 40, else F. A warning is −8, a critical finding is −20.
  The worked example is two warnings and one critical: 100 − 8 − 8 − 20 = 64, grade C. The previous
  page showed three warnings plus one critical and still claimed 64. Do not "fix" it back.
- A modelled saving is bytes, not money.
- Helm request floor (committed defaults, totals across replicas): CPU 3.35 cores, memory 6.00 GiB.
  Reproduce with `make deployment-footprint`. `scannerWorker` is not the scanner CronJob.
- PostgreSQL and Redis are required and external to the chart. Engines stay outside the chart.
- Comparison table on Reference matches the README caveats (Cryo sorting is Spark-only and Hilbert
  is capability-gated; DIY maintenance only if you build it; managed-cloud sorting and health vary).
- Pre-1.0 launch surface: scanner, planner, governor, Trino and Spark engines, presets, audit, run console.
- Quickstart needs Docker, Python 3.12+, Node 22+. Commands: clone `tech-pw/cryo`, `make up`,
  `make seed`, open `http://localhost:5173`.

Heading ids that diagram links depend on: `concepts/#scan-path`, `maintenance/#chunking`,
`maintenance/#admission`, `architecture/#component-jobs`, `reference/#public-surfaces`, `engines/`.

## Where the code is

| Path | Role |
|---|---|
| `site/_src/site.json` | Nav, repo URL, tagline |
| `site/_src/pages/*.html` | Page fragments. Front matter: `title`, `lede`, `description`, `updated` |
| `site/scripts/build.mjs` | Chrome, TOC, search index, sitemap, `404.html`. Components listed in `site/README.md` |
| `site/scripts/diagrams.mjs` | 15 diagram specs. `renderDiagram(name)` returns svg, alt, legend |
| `site/scripts/highlight.mjs` | Bash and SQL only |
| `site/scripts/preview.mjs` | Watch and serve |
| `site/assets/css/site.css` | All visual design |
| `site/assets/js/site.js` | Theme, search, drawer, scrollspy, copy, tabs, diagram highlight, expand, copy-as-markdown |
| `site/assets/search.json` | Generated |
| `site/assets/img/og.png` | Static 1200×630. Regenerate if the tagline changes |
| `site/index.html` and `site/<slug>/index.html` | Generated. Edit the fragment, then rebuild |
| `site/diagrams/` | Archify viewers. Not embedded |

Internal links in fragments are `href="@/maintenance/#admission"`. The build rewrites them per page.
Edge anchors in diagrams are `"node.side±offset"` (`t`, `r`, `b`, `l`) and must stay orthogonal.
A diagonal segment is a bug.

Deleted and must stay deleted: `site/assets/js/charts.js`, `site/assets/js/index.json`,
`site/scripts/build-search-index.mjs`.

The earlier creative plan is superseded.
It describes the chart engine and embedded Archify frames. Do not implement it.

## What was verified

Headless Chrome against the local server, desktop 1440 and phone 390, light and dark: home,
architecture (system and sequence), maintenance, engines tabs, health grades, reference table,
deployment, search for "admission", mobile drawer. A link checker reported no missing files or
heading ids. Theme toggle, Spark tab, diagram hover highlighting, Expand, and Copy page as
Markdown were exercised. `node site/scripts/build.mjs --check` passed after the last generator run.
The palette pass was screenshotted the same way. The user then said light mode was the weak one;
the palette change above is the response, and they have not reviewed it yet.

## Still open

- User review of light mode after the palette pass. Do not restyle dark mode unless they ask.
- User review of the diagram refresh.
- The build's label-width warnings are conservative estimates. The six that print today were
  checked in screenshots and fit; re-check visually if you change any of those labels.
- On a phone, large diagrams pan inside the frame. Expand and the text description are the fallback.
  Do not shrink type below the current sizes to make them fit.
- `404.html` only renders with its CSS once it is on `https://tech-pw.github.io/cryo/`.
- `docs-pages.yml` has not been executed.
- The site is uncommitted. Generated HTML must be regenerated and included in the same commit as
  any fragment, CSS, JS, or diagram change, because CI fails when output is stale.

## Do not

- Add fake charts, illustrative fleet counts, or currency.
- Couple sources and destinations, or emit SQL from the planner, in diagrams or prose.
- Put orange anywhere except the single marker flag.
- Swap body ink back to deep ice on the light page.
- Put white text on `#277ABE` at small sizes.
- Bring back solid filled diagram boxes, boxed edge labels, or 2px+ strokes.
- Edit generated `site/*/index.html` by hand. Edit `site/_src/pages/` and rebuild.
- Commit or deploy without an explicit ask.
