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
