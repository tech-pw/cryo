# Diagrams

These interactive HTML diagrams are generated with [Archify](https://github.com/tt-a1i/archify)
and checked into the Pages artifact so the public site stays portable and does not depend on a
runtime diagram service. The adjacent JSON files are the editable diagram sources; the HTML
files are the self-contained viewer outputs. The main site no longer embeds them: its own
diagrams are generated at build time from `site/scripts/diagrams.mjs`. These viewers are kept as
optional deep dives (route tracing, presentation mode) and are linked from the Architecture page.

- `system.architecture.html` — architecture — Cryo reads catalog metadata into the Scanner and writes Iceberg data from Workers through Trino or Spark on EMR.
- `scan.dataflow.html` — dataflow — The default scan moves catalog metadata into PostgreSQL; object storage is not on that path.
- `maintenance.sequence.html` — sequence — Scheduler enqueues, Governor probes live load, the Worker asks the Planner, the engine executes, and the audit is written.
- `job.lifecycle.html` — lifecycle — A job is queued, admitted, then running, and ends succeeded, failed, or deferred; a failure does not stop the run.
- `assign.workflow.html` — workflow — Filter tables, select every match as a filter spec, assign a preset, preview the plan, then run.

The five Archify exports share one runtime: `archify.css` and `archify.js` hold the stylesheet and script that every export used to inline (about 600 KB each, which kept the published site at its 15 MB budget). After re-exporting a diagram, replace its inline main `<style>` and `<script>` with `<link rel="stylesheet" href="archify.css">` and `<script src="archify.js"></script>`. Keep `<style id="archify-fonts">` inline: the SVG export reads it by id.
