# Scanner: separating "broken table" from "scan failure" — 2026-09-23

## What prompted this

A scan of the `cdp.central` schema (387 requested tables, 585 discovered, 4 chunks) came back
`partial_failed`: **384 scanned, 201 failed**. The failures were uniform across all four chunks
— roughly 146 assigned / 96 scanned / 50 failed each — which is the shape of a systematic data
problem, not a flaky scanner.

Every one of the 201 was the same error:

```
RuntimeError: load manifests: could not open manifest file: blob
(key "silver/central/<table>/metadata/snap-<id>-1-<uuid>.avro") (code=NotFound):
operation error S3: GetObject, https response error StatusCode: 404,
api error NoSuchKey: The specified key does not exist.
```

The table's own metadata file names a manifest list that is not in S3. A genuine 404, not an
`AccessDenied` wearing a 404's clothes.

## This is not Cryo's bug

Cross-checked against Trino, reading the same Hive metastore:

| Table | Cryo | Trino |
|---|---|---|
| `central.gold_polls_evolving` | missing `snap-2836956739…d2373dc1….avro` | `Metadata not found in metadata location` |
| `central.gold_banners_evolving` | missing manifest | `Error accessing metadata file` |
| `temp.abhisek_duration` (control) | scanned OK | 7,661,016 rows |

Trino names the *same* missing file. These tables are unreadable for every consumer. Their
metastore `metadata_location` points at `00000-*.metadata.json` — the very first metadata file
a table ever writes — which suggests the catalog pointer never advanced while the snapshot it
references was later expired away.

## The problem with counting these as failures

Re-running the scan produces the identical error, forever. Every six hours. Counting them in
`failed` means the scan failure rate can never reach zero no matter how healthy the scanner is,
which makes the metric useless as an alarm: you cannot tell "201 tables the lakehouse lost" from
"the scanner is broken". It also buries the list of tables somebody needs to actually go fix.

## What changed

**`cryo/scanner/failure_classifier.py`** — reused rather than adding a parallel classifier. It
already turned `last_scan_error` text into an operator-facing category, so the new signature
went there:

- New category `manifest_missing`, matched only when the error names *both* a missing object
  (`NoSuchKey` / `code=NotFound` / `404`) *and* a manifest. Both halves are required: `NoSuchKey`
  alone also covers a data file vanishing mid-scan, which is a different and often transient
  problem.
- `metadata_not_found` extended to the same two-part shape for a missing `.metadata.json`.
  This was the gap in the first cut: the `cdp.central` sample above is the *manifest* flavour,
  but fleet-wide it is the minority. Replaying a full chunk's error text from the 2026-09-23
  00:00 UTC run through the classifier gives **265 `metadata_not_found`, 74 `manifest_missing`,
  1 `permission_denied`, 1 `other`** — so keying only on manifests caught 74 of 341, and the
  other 265 kept counting as retryable failures. The metadata flavour names no manifest and the
  Go engine words it as a 404, so it matched neither the manifest rule nor the pre-existing
  `Path does not exist` one.
- `permission_denied` also matches a bare HTTP 403 (`code=Unknown`, no `ACCESS_DENIED` string),
  which is how the Go engine reports a denied read. Still non-permanent, so this only fixes the
  label; one table in that run was affected.
- `scan_failure_is_permanent(error)` — true for `manifest_missing` and the pre-existing
  `metadata_not_found`. **`permission_denied` is deliberately excluded**: an IAM policy change
  does fix those, and burying thousands of them would hide a correctable misconfiguration.

**`cryo/scanner/run.py`** — both the serial and parallel scan paths classify at the point of
failure. Permanent failures increment `ScanSummary.unreadable` instead of `failed`, and log at
`warning` rather than `exception` (a full stack trace for a known-dead table is noise).

**`cryo/db/models.py` + migration `20260923_0200`** — `tables.last_scan_error_kind`, nullable,
`"failed"` or `"unreadable"`. This stores the scanner's own retryability decision, not a cached
copy of the derived category, so it cannot drift from the classifier. It exists because
filtering the fleet for unreadable tables needs a real column: it's wired into the predicate
field map, the sort map, and the tables CSV export.

**`cryo/db/repositories/scanner.py`** — `unreadable` is carried through chunk claim, progress,
finish, overflow-trimming, and the run rollup, with its own `unreadable_tables` list kept out of
`failed_tables`/`last_error`. A run whose only non-scanned outcome is unreadable now reports
**`succeeded`**, not `partial_failed`.

## How to get the actionable list

Once every source has been rescanned since this shipped:

```
GET /api/v1/tables?filter={"all":[{"field":"last_scan_error_kind","op":"=","value":"unreadable"}]}
```

or export the tables CSV, which now carries `last_scan_error_kind` alongside `last_scan_error`.
Per-run, the offenders are in the run summary's `unreadable_tables`.

## Related, same day

- **`cdp_dev` source disabled.** Its metastore (`trino-metastore-v2.penpencil.co` →
  `10.1.148.96`, `10.1.161.70`, `10.1.216.161`) times out on all three IPs from the `cryo`
  namespace. `cryo-api` can't reach it either, so this predates any recent change. 1,164 active
  tables (9.1% of the fleet) are now a deliberate blind spot until DevOps restores networking.
- **Source-scoping fix.** `_discover_candidates` used to connect to every enabled source even
  when all requested tables lived in one of them, so `cdp_dev`'s dead metastore stalled scans of
  healthy `cdp` tables. A five-table scan went from stalling past ten minutes to finishing in
  13.1 seconds.

## What this does not claim

The 201 tables are still broken. Nothing here repairs them — it only stops them from being
reported as a scanner problem and makes them enumerable. The fix is out-of-band: the team that
owns those tables has to repoint the catalog or restore them.

Whether the fleet-wide ~10.7% scan failure rate is mostly this same corruption is now
*measurable* (compare `unreadable` vs `failed` on the next full wave) but not yet measured.

## Follow-up, 2026-09-24: stop retrying them every six hours

Measured: on 2026-09-24, 1,353 tables failed a scan. 1,227 of them were already deactivated
dead `*_evolving` entries, and most had failed 20+ times in a row. Two things kept them
coming back:

- **Scans reactivated them.** `upsert_table` set `is_active = True` on every discovery, so each
  6-hourly scan undid the scheduler's `EvolvingCatalogCleanupRunner`. The scan then failed, and
  the daemon deactivated them again. Reactivation now happens only on a successful scan, in
  `record_table_scan_outcome`.
- **Nothing spaced out the retries.** A table that never scanned successfully has no stats row,
  so `already_scanned_today` never skipped it, and `order_by_staleness` sorted it first as
  "never scanned".

**Retry ladder** (`cryo/scanner/persistence.py::SCAN_RETRY_LADDER`), keyed on
`consecutive_scan_failures` and `last_scan_error_at`, so no migration was needed:

| Failure streak | Next scheduled retry |
|---|---|
| 1–2 | after 1 day (same cadence as a healthy table) |
| 3 | after 7 days |
| 4 | after 15 days |
| 5+ | after 30 days |

Every rung is due 3 hours early, because a table can be reached hours into a run and would
otherwise slip a whole 6-hour slot. Any success resets the streak. Tables in backoff count as
`ScanSummary.deferred`. In the ad-hoc run UI they show under "skipped", and they never touch
the catalog or S3. A full scan or a scan of explicit `table_ids`/`table_names` ignores the
ladder, so the way to re-check a repaired table is to scan it by name. Tables that already
had long streaks go straight to the 30-day rung; on prod data the next scheduled scan skips
1,353 guaranteed-to-fail retries.

**Classifier.** A catalog `metadata_location` with no bucket (Go error `failed to load table:
open /youtube/videos/metadata/….metadata.json: invalid argument`) is now `metadata_not_found`.
The four `cdp.youtube` tables that fail this way (`silver_videos`, `silver_channels`,
`silver_comments`, `silver_video_metrics`) have NULL locations and a working `*_fixed` copy
rebuilt by the owning team, so they are dead entries, not a scanner bug.

**Daily digest "Scan Health" block** (`cryo/reporting/scan_health.py`). It covers every table
row on an enabled source, active or not: tables in catalog, passed (last attempt succeeded),
corrupt (metadata or manifest gone from S3), failed (permissions or other, fixable), not yet
scanned, and how many failing tables sit on each retry rung. First numbers on prod,
2026-09-24: **12,970 in catalog: 11,435 passed, 1,489 corrupt, 46 failed**.
