import type { TableSnapshot } from "../../api/snapshots";

const parseCompare = (raw: string | null): [string, string] | null => {
  if (!raw) return null;
  const [left, right] = raw.split(",").map((part) => part.trim());
  if (!left || !right) return null;
  return [left, right];
};

export const SnapshotSelection = ({
  snapshots,
  selectedId,
  compareRaw
}: {
  snapshots: TableSnapshot[];
  selectedId: string | null;
  compareRaw: string | null;
}) => {
  const selected = snapshots.find((row) => row.snapshot_id === selectedId) ?? null;
  const compare = parseCompare(compareRaw);
  const left = compare ? snapshots.find((row) => row.snapshot_id === compare[0]) : undefined;
  const right = compare ? snapshots.find((row) => row.snapshot_id === compare[1]) : undefined;

  return (
    <div className="mb-4 space-y-3">
      {selectedId ? (
        <section aria-label="Selected snapshot">
          <h3 className="text-sm font-semibold">Selected snapshot</h3>
          {selected ? (
            <p className="font-mono text-xs break-all">
              {selected.snapshot_id} · {selected.operation ?? "unknown"} ·{" "}
              {selected.committed_at ?? "time not recorded"}
            </p>
          ) : (
            <p className="text-xs text-muted-foreground">
              Snapshot {selectedId} is not in the loaded page.
            </p>
          )}
          <p className="text-xs text-muted-foreground">
            Schema change markers are not shown. This snapshots read does not include historical schema IDs.
          </p>
        </section>
      ) : null}
      {compare ? (
        <section aria-label="Summary comparison">
          <h3 className="text-sm font-semibold">Summary comparison</h3>
          <p className="text-xs text-muted-foreground">Exact file diff is not computed.</p>
          {!left || !right ? (
            <p className="text-xs text-muted-foreground">One or both snapshots are outside the loaded page.</p>
          ) : (
            <p className="text-xs">
              {left.snapshot_id} → {right.snapshot_id}. Records and size deltas are shown only when both summaries
              report them.
            </p>
          )}
        </section>
      ) : null}
    </div>
  );
};
