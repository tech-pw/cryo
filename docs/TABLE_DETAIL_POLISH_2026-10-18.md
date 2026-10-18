# Table detail polish (2026-10-18)

A section-by-section review of the table page found duplicated cards, sections on the wrong tab,
and copy that claimed more than the data showed. This records what changed and why, so the next
change doesn't move things back.

## Placement

| Section | Was | Now | Why |
|---|---|---|---|
| Optimization checklist | Quality tab | Overview, "Layout checks" (`#overview-checks`) | It explains the health deductions. On Quality it read as a third score. |
| Archive | Usage tab | Cost & Savings (`#archive`) | It's a cost lever, and it now executes. See `ARCHIVE_EXECUTION_2026-10-18.md`. |
| Properties | Schema & files | Removed; Config owns properties (`#config-properties`) | It duplicated the Config inventory. |
| Preset & overrides pointer | Half-width card on Maintenance | A link in the Readiness header | The card was only a signpost. |
| Excluded from Cryo expiry | Below the 25-row commit table | Directly under the Snapshots summary | It's what an operator checks before expiring. |

Old anchors still resolve through `TABLE_HASH_ALIASES` in `web/src/pages/table-detail/tabAliases.ts`:
`files-properties`, `hq-checklist` and `quality-checklist` redirect to their new homes, and
`maintenance-settings` now opens Config. The alias map is applied to any `?tab=`, so a stale tab in
an old link doesn't strand the hash.

## Honesty fixes

- **Settings drawer, compliance:** pending deletions come from `DeletionTracking` when present, with
  the date expiry removes them. ABAC policies are labelled "recorded, not enforced", because no read
  path enforces them. The response has `abac_enforced: false` and `protected_snapshot_count`.
- **Config, effective parameters:** effective = preset merged with overrides (`routes.py`
  `get_effective_params`), so each row shows Preset or Override instead of "Source not recorded".
- **Config, provenance:** plain sentences ("Assigned to this table by an operator") replace the
  "Preset provenance platform default" label.
- **Schema & files:** range, distinct-value and percentile columns show only when recorded; a dash
  marks a single missing value. The trend chart drops the grade column when no grade exists, which
  removed a fake "Not measured" legend.
- **Cost & Savings:** the separate-estimate disclaimers became one "Ways to save" card with three
  levers side by side, each naming its input. Request counts appear only when measured.
- **Run detail:** "Manual · manual" became "Manual", and other triggers are humanised.
- **Engine gaps:** an operation the engine can't render (`rewrite_position_deletes` on Trino) is
  dropped by the planner with a note (`gate_engine_unsupported`) rather than failing the run.

## Visual changes

- Maintenance: Readiness spans the row with four fact tiles. The run-grid scroll container got
  4px padding so the selected outline isn't clipped. Compaction analysis leads with five summary
  tiles, shows one ranking at a time, lists triggered signals first and collapses the rest, and
  hides the MOOP column when nothing recorded it.
- Config: the workflow is a single summary band (name, how it was chosen, parameter and override
  counts, Change workflow), followed by a full-width parameter table.

## Docs site

Screenshots come from `web/e2e/docs-capture/` (`npx playwright test -c
e2e/docs-capture/playwright.config.ts` from `web/`). New shots: `table-tab-overview-checks`,
`table-tab-cost-archive`, `table-archive-dialog`. The demo fixtures now carry a last commit,
runs whose step timings match their audit receipts, a pending deletion, archived partitions, an
archive job and a preflight with one missing permission.
