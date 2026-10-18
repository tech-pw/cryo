# Connections page redesign (2026-10-16)

PR: [tech-pw/cryo#67](https://github.com/tech-pw/cryo/pull/67). Branch `feat/connections-page-redesign`.

## Problem

Settings → Connections used wide `DataTable`s inside a settings column. On ordinary laptop widths
the tables overflowed the page horizontally. Each row carried six or more columns of raw config,
and the same layout repeated on every tab (Lakehouses, Engines, AI providers, Slack). The Engines tab mixed
the engines Cryo ships with and the clusters operators connected, even though changing a shipped engine has
very different consequences.

## What changed

- **Row cards instead of tables.** `ConnectionList.tsx` provides `ConnectionSection`, `ConnectionList`
  and `ConnectionRow`. Each row shows an icon, the name, the kind (for example "Native (iceberg-go)" or
  "Trino"), a status dot, badges, and a short `details` line. Actions sit on the right. Rows wrap
  instead of overflowing, and styles live in `connections.css`.
- **Per-kind summaries** live in `connectionSummary.ts`, a pure module with tests:
  - lakehouse: catalog type, endpoint and S3 auth method. `redactUri` strips any credentials from
    the endpoint before display.
  - engine: slot cap, Trino catalog, and off-peak window.
- **Engines are split into two sections** in `EnginesTab.tsx`:
  - **Cryo engines**: `isCryoEngine()` is true for `engine == "native"` and for `spark_emr` with
    `config.submit_mode == "k8s"`. These are seeded by migrations and run inside the deployment.
  - **Your engines**: everything else (Trino, EMR Serverless, Flink). **New engine** lives here.
- **Cryo engines are editable, with a caveat.** Edit and Disable open a `ConfirmAction` whose text
  comes from `cryoEngineCaveat()`:
  - native: slot cap and timeouts bound every job routed to it; presets pick up the change on
    their next run.
  - Spark on Kubernetes: the nightly batch needs exactly one enabled Spark engine.
  - The edit confirmation adds "Your edits persist across Cryo upgrades."
  - Deleting a Cryo engine warns that Cryo will not re-create it.
  - Enabling a disabled Cryo engine needs no confirmation.
- The **Default preset** badge marks the engine targeted by the enabled default preset. It can
  appear in either section.

No backend or API changes.

## Docs site

- `site/_src/pages/concepts.html`: the lakehouse prose, alt text and caption describe the row layout.
- `site/_src/pages/onboarding.html`: the "Done when" line matches the new list (an **Enabled**
  badge, the endpoint and the S3 auth method).
- `site/_src/pages/engines.html`: a new "Cryo engines and your engines" section, with the
  `connections-engines` screenshot and a table of the confirmation caveats.
- Screenshots: `connections-{light,dark}.png` were regenerated, and `connections-engines-{light,dark}.png`
  are new. The capture SHOT is in `web/e2e/docs-capture/capture.ts`. The demo data adds a
  `cryo-native` engine so both sections render.
- `capture.ts` waits up to 60 s for each shot's `waitFor` text. The first Vite compile of a lazy
  route can exceed 15 s, and the old timeout captured a loading skeleton.
