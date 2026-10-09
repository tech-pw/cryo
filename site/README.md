# Cryo docs site

Static site for GitHub Pages, served at `https://tech-pw.github.io/cryo/`.

```bash
make docs-preview                    # rebuild, serve http://localhost:4173, rebuild on save
make docs                            # render site/_src into site/ once (commit the output)
node site/scripts/build.mjs --check  # fail if committed output is stale (CI runs this)
```

`make docs-preview` watches `site/_src`, `site/scripts` and `site/assets`, regenerates the pages, and serves them with caching turned off. A browser refresh shows the latest edit. Set `DOCS_PORT` to use another port.

No dependencies. Node 22.

| Path | What it is |
|---|---|
| `_src/site.json` | Navigation groups and site metadata |
| `_src/pages/*.html` | Page fragments with front matter (`title`, `lede`, `description`, `updated`) |
| `scripts/build.mjs` | Generator: chrome, TOC, search index, sitemap, `llms.txt`, per-page `index.md` |
| `scripts/diagrams.mjs` | Diagram specs rendered to inline SVG at build time |
| `scripts/highlight.mjs` | Small bash and SQL highlighter |
| `assets/` | CSS, JS, self-hosted IBM Plex (reading) and Cormorant Garamond (homepage display, OFL), Cryo mark SVGs (`assets/svg/cryo-mark.svg`, `cryo-mark-small.svg`, `favicon.svg`), OG image |
| `diagrams/` | Standalone Archify viewers, optional deep dives |

Everything outside `_src/` and `scripts/` is generated or static and is what gets published.

Each page also writes `index.md`, and the site root writes `llms.txt`. HTML pages link the markdown with `rel="alternate" type="text/markdown"`. Copy page fetches that file. There is no chat on the site. The quickstart carries the prompt an agent should be given: read `llms.txt`, then answer only from those pages.

## Writing a page

Fragments are plain HTML plus a few components. Link internally with `href="@/maintenance/#admission"`,
which the build rewrites relative to each page.

```html
<callout type="note|tip|caution|important" title="Optional title"><p>...</p></callout>
<codeblock lang="bash|sql" title="terminal">
$ make up
</codeblock>
<diagram name="chunking" caption="One sentence." />
<tabs group="engine"><tab title="Trino">...</tab><tab title="Spark">...</tab></tabs>
<steps><step title="Clone">...</step></steps>
<linkcards><linkcard href="@/health/" title="Health">Why.</linkcard></linkcards>
<screenshot name="runs" url="cryo.local/runs" alt="What the image shows, with its numbers." caption="One sentence." />
```

The homepage closer is a native `<video>` in `site/_src/pages/index.html`, not a component. It plays
`assets/video/homepage-intro-video.mp4`, a byte copy of `videos/cryo-showreel-30s/renders/16x9.mp4` (1080p, 60 fps,
H.264 High, faststart already set), from a poster, with `preload="none"`. Do not re-encode it. A second
pass, even at CRF 18, drops the frame rate and softens the type. Copy when the film changes:

```bash
cp videos/cryo-showreel-30s/renders/16x9.mp4 site/assets/video/homepage-intro-video.mp4
# Poster is a designed title card (page fonts + cryo-mark), not a film frame.
# The native control bar covers the bottom fifth, so keep type in the upper half.
# Source lives with the film; re-screenshot at 1280×720 if the line changes.
```

Rules: no invented numbers in prose or fake dashboards (screenshots are the real UI on synthetic demo data, and every frame is badged "Demo data"), sentence-case headings, no uppercase eyebrows.
Brand and layout rationale: `docs/DOCS_SITE_DESIGN_RESEARCH_2026-10-05.md`.
Current working state: `docs/DOCS_SITE_REDESIGN_HANDOFF_2026-10-05.md`.

## Screenshots

`<screenshot name="x">` shows `assets/img/screens/x-light.png` or `x-dark.png` to match the theme, in a
browser frame with a "Demo data" badge, and enlarges on click. The images are the real web UI
captured by Playwright against synthetic data in `web/e2e/docs-capture/demo-data.ts`. Nothing in
them comes from a deployment. Regenerate after UI changes:

```bash
cd web && npx playwright test -c e2e/docs-capture/playwright.config.ts
```

The capture config starts Vite on :5173 and uses the installed Chrome at 2× pixel density. Demo
scores must stay reachable under the grading rule (100 minus 8 per warning and 20 per critical),
because the Health page explains them. Add a screen by adding an entry to `SHOTS` in `capture.ts`;
the run logs any API call the demo data does not answer.

## Adding a diagram

Add a spec to `diagrams` in `scripts/diagrams.mjs`. Edges anchor to `"node.side±offset"` and route
orthogonally. Every diagram gets a legend and a text description generated from its edges. Check
it at 1440px, at 390px, and in dark mode before committing.

Keep the viewBox 820–1056 wide so it renders near 1:1. The build prints a warning when a label
probably overflows its card; the estimate is conservative, so confirm with a screenshot.

| Where | Field | Meaning |
|---|---|---|
| node | `kind` | `cryo` (default), `source`, `engine`, `store`, `ui`, `state`, `ghost` |
| node | `icon` | glyph name from `GLYPHS`; each kind has a default |
| node | `tone` | for `state` nodes: `ok`, `warn`, `bad`, `info` |
| node | `code` | `"label"` or `"sub"` sets that line in Plex Mono |
| node | `marker` | the one orange flag in the diagram |
| node | `href` | makes the card a link |
| node | `step` | number badge on the card's left edge, for a vertical checklist |
| edge | `kind` | `write` (default), `read`, `ctl`, or `no` (dotted with a ✕, "not read") |
| edge | `via` | extra corner points, `[[x, y], ...]` |
| edge | `lseg`, `lside`, `lpos` | label segment (negative counts from the end), side `t b l r c`, or exact point |
| edge | `port` | `false` hides the start dot |
| edge | `step` | number badge on the line; numbered edges are the main path |
| edge | `main` | main path without its own number (a parallel branch of a step) |
| lane | `kind` | `read`, `cryo`, `write`, `plain`, or `title` (heading only, no box) |
| lane | `label` | `"Name (qualifier)"` renders the qualifier muted |
| sequence | `xs`, `headW`, `rowH`, `bands` | participant x positions, head width, row pitch, phase bands |

Colours come only from the `--d-*` variables in `assets/css/site.css`, in both themes.

Make it readable at a glance:

- The `title` is the takeaway, written as a sentence ("A job starts only when the engine has room
  for it"), not a topic ("Load-aware admission"). It is the figure's visible headline.
- Number the main path with `step`. Once any edge has a step, unnumbered edges render as muted side
  paths, so keep side paths unnumbered on purpose.
- Subtitles say what a part does ("checks live load"), not what it is built with ("FastAPI").
- Start the `desc` with the steps in order, so the text description tells the same story.
