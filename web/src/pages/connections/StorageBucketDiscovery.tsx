import { Database, Loader2, RefreshCw } from "lucide-react";
import { Button } from "../../components/ui/button";
import { cn } from "../../lib/cn";
import type { StorageDiscoveryState } from "./useStorageDiscovery";

/**
 * Buckets the catalog's tables actually live in, so the operator picks one instead of
 * recalling it. The walk reads a table-level location per database slice and takes well
 * over a minute on a real metastore, so this renders buckets as they arrive -- the common
 * ones appear within seconds -- rather than showing a spinner until the sweep completes.
 */
export const StorageBucketDiscovery = ({
  state,
  selectedBucket,
  onSelect,
  onRetry
}: {
  state: StorageDiscoveryState;
  selectedBucket: string | null;
  onSelect: (bucket: string) => void;
  onRetry: () => void;
}) => {
  const { status, buckets, scanned, total, warnings, error } = state;

  if (status === "idle") return null;

  // Nothing usable came back at all -- fall through to manual entry rather than leaving
  // the operator staring at a failed lookup with no next step.
  if (status === "error" && buckets.length === 0) {
    return (
      <div className="rounded-[var(--radius-default)] border border-border p-3">
        <p className="text-sm font-medium">Couldn&apos;t list buckets automatically</p>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {error ?? "The catalog didn't return any storage locations."} Enter the region and
          endpoint below by hand instead.
        </p>
        <Button type="button" size="sm" variant="secondary" className="mt-2" onClick={onRetry}>
          <RefreshCw />
          Try again
        </Button>
      </div>
    );
  }

  const running = status === "running";
  const percent = total > 0 ? Math.min(100, Math.round((scanned / total) * 100)) : 0;

  return (
    <div
      aria-live="polite"
      aria-busy={running}
      className="rounded-[var(--radius-default)] border border-border p-3"
    >
      <div className="flex items-center gap-2">
        {running ? (
          <Loader2
            className="size-4 shrink-0 animate-spin motion-reduce:animate-none"
            aria-hidden="true"
          />
        ) : null}
        <p className="text-sm font-medium">
          {running
            ? "Finding your S3 buckets…"
            : buckets.length > 0
              ? `Found ${buckets.length} bucket${buckets.length === 1 ? "" : "s"} in this catalog`
              : "No object-store buckets found"}
        </p>
        {total > 0 ? (
          <span className="ml-auto text-xs tabular-nums text-muted-foreground">
            {scanned}/{total} schemas
          </span>
        ) : null}
      </div>

      {running ? (
        <div
          className="mt-2 h-1 w-full overflow-hidden rounded-full bg-[var(--color-surface-sunken)]"
          role="progressbar"
          aria-valuenow={percent}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div
            className="h-full bg-[color:var(--color-status-success)] transition-[width] duration-[var(--motion-base)] ease-[var(--ease-out)] motion-reduce:transition-none"
            style={{ width: `${percent}%` }}
          />
        </div>
      ) : null}

      <p className="mt-1.5 text-xs text-muted-foreground">
        {running
          ? "Reading where each schema's tables are stored. Buckets appear here as they're found — you can pick one without waiting for the rest, or keep filling in the fields below."
          : buckets.length > 0
            ? "Selecting one records it on this lakehouse — Cryo still authenticates with the method below."
            : `Checked ${total} schema${total === 1 ? "" : "s"} — none record an s3:// (or gs://, abfss://) location. Normal for a local or HDFS warehouse; fill in the fields below manually.`}
      </p>

      {buckets.length > 0 ? (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {buckets.map((bucket) => (
            <button
              key={bucket}
              type="button"
              onClick={() => onSelect(bucket)}
              className={cn(
                "flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors duration-[var(--motion-fast)]",
                selectedBucket === bucket
                  ? "border-foreground bg-[var(--color-surface-sunken)] text-foreground"
                  : "border-border text-muted-foreground hover:text-foreground"
              )}
              aria-pressed={selectedBucket === bucket}
            >
              <Database className="size-3" aria-hidden="true" />
              {bucket}
            </button>
          ))}
        </div>
      ) : null}

      {/* Slices the walk had to skip. Shown once it has settled so the bucket list is never
          quietly presented as complete when part of the catalog was unreadable. */}
      {!running && warnings.length > 0 ? (
        <p className="mt-1.5 text-xs text-muted-foreground">
          {warnings.length} schema group{warnings.length === 1 ? "" : "s"} couldn&apos;t be read,
          so this list may be incomplete.
        </p>
      ) : null}

      {!running && error && buckets.length > 0 ? (
        <p className="mt-1.5 text-xs text-muted-foreground">{error}</p>
      ) : null}
    </div>
  );
};
