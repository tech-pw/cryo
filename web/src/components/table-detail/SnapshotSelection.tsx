import { useSearchParams } from "react-router-dom";
import type { TableSnapshot } from "../../api/snapshots";
import { formatBytesGb, formatNumber, formatTime } from "../../lib/format";

const parseCompare = (raw: string | null): [string, string] | null => {
  if (!raw) return null;
  const parts = raw.split(",").map((part) => part.trim());
  if (parts.length !== 2 || !parts[0] || !parts[1] || parts[0] === parts[1]) return null;
  return [parts[0], parts[1]];
};

export const snapshotDelta = (before: number | null | undefined, after: number | null | undefined): number | null =>
  typeof before === "number" && Number.isFinite(before) && typeof after === "number" && Number.isFinite(after) ? after - before : null;

const deltaLabel = (value: number | null, bytes = false): string => {
  if (value == null) return "Not reported";
  return `${value > 0 ? "+" : value < 0 ? "−" : ""}${bytes ? formatBytesGb(Math.abs(value)) : formatNumber(Math.abs(value))}`;
};

export const SnapshotSelection = ({
  snapshots, selectedId, compareRaw
}: {
  snapshots: TableSnapshot[];
  selectedId: string | null;
  compareRaw: string | null;
}) => {
  const [params, setParams] = useSearchParams();
  const selected = snapshots.find((row) => row.snapshot_id === selectedId) ?? null;
  const compare = parseCompare(compareRaw);
  const left = compare ? snapshots.find((row) => row.snapshot_id === compare[0]) : undefined;
  const right = compare ? snapshots.find((row) => row.snapshot_id === compare[1]) : undefined;
  if (!selectedId && !compare) return null;

  return (
    <div className="td-snapshots-selection">
      {selectedId ? (
        <section aria-label="Selected snapshot">
          <h3 className="text-sm font-semibold">Selected snapshot</h3>
          {selected ? (
            <>
              <p className="mt-2 text-sm"><span className="break-all font-mono">{selected.snapshot_id}</span> · {selected.operation ?? "Operation not recorded"} · {formatTime(selected.committed_at)}</p>
              <label className="mt-3 flex flex-wrap items-center gap-2 text-xs">
                Compare against
                <select className="max-w-full rounded border border-border bg-card px-2 py-1.5" value={compare?.[0] === selectedId ? compare[1] : ""} onChange={(event) => {
                  const next = new URLSearchParams(params);
                  if (event.target.value) next.set("compare", `${selectedId},${event.target.value}`);
                  else next.delete("compare");
                  setParams(next, { replace: true });
                }}>
                  <option value="">Choose a snapshot from this page</option>
                  {snapshots.filter((row) => row.snapshot_id !== selectedId).map((row) => <option key={row.snapshot_id} value={row.snapshot_id}>{row.snapshot_id} · {row.operation ?? "Unknown operation"}</option>)}
                </select>
              </label>
            </>
          ) : <p className="mt-2 break-all text-xs text-muted-foreground">Snapshot {selectedId} is not in the loaded page.</p>}
        </section>
      ) : null}
      {compare ? (
        <section aria-label="Summary comparison">
          <h3 className="text-sm font-semibold">Summary comparison</h3>
          {!left || !right ? <p className="mt-2 text-xs text-muted-foreground">One or both snapshots are outside the loaded page.</p> : (
            <>
              <p className="mt-2 break-all font-mono text-xs">{left.snapshot_id} → {right.snapshot_id}</p>
              <dl className="td-snapshot-deltas">
                <div><dt>Records change</dt><dd>{deltaLabel(snapshotDelta(left.summary.total_records, right.summary.total_records))}</dd></div>
                <div><dt>Total file size change</dt><dd>{deltaLabel(snapshotDelta(left.summary.total_files_size, right.summary.total_files_size), true)}</dd></div>
                <div><dt>Records in comparison</dt><dd>{right.summary.total_records == null ? "Not reported" : formatNumber(right.summary.total_records)}</dd></div>
                <div><dt>File size in comparison</dt><dd>{right.summary.total_files_size == null ? "Not reported" : formatBytesGb(right.summary.total_files_size)}</dd></div>
              </dl>
            </>
          )}
          <p className="mt-3 text-xs text-muted-foreground">Changes are comparison minus selected snapshot, using recorded totals. This is a summary comparison; exact file differences and historical schema changes are not available here.</p>
        </section>
      ) : null}
    </div>
  );
};
