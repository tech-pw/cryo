import { AlertTriangle, Check, Database, Loader2, Lock, RefreshCw } from "lucide-react";
import { useState } from "react";
import type { BucketAccess, DiscoveredBucket } from "../../api/client";
import { Alert } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { cn } from "../../lib/cn";
import type { StorageDiscoveryState } from "./useStorageDiscovery";

/**
 * Buckets holding fewer than this share of the catalog's tables are folded away by
 * default. Real catalogs are lopsided -- one measured fleet put 99.5% of tables in four
 * of twelve buckets, the rest being dev leakage, `-audit` siblings and one-table scratch
 * buckets -- so showing all of them equally buries the decision in noise.
 */
const SIGNIFICANT_SHARE = 0.01;

const ACCESS_LABEL: Record<BucketAccess, string> = {
  readable: "Readable",
  denied: "No access",
  missing: "Not found",
  unknown: "Unchecked"
};

const AccessBadge = ({ bucket }: { bucket: DiscoveredBucket }) => {
  const { access, access_detail: detail } = bucket;
  if (access === "readable") {
    return (
      <span
        className="flex items-center gap-1 text-[color:var(--color-status-success)]"
        title={ACCESS_LABEL[access]}
      >
        <Check className="size-3" aria-hidden="true" />
        {ACCESS_LABEL[access]}
      </span>
    );
  }
  if (access === "unknown") {
    return <span className="text-muted-foreground">{ACCESS_LABEL[access]}</span>;
  }
  return (
    <span
      className="flex items-center gap-1 text-[color:var(--color-status-warning)]"
      title={detail ?? ACCESS_LABEL[access]}
    >
      <Lock className="size-3" aria-hidden="true" />
      {ACCESS_LABEL[access]}
    </span>
  );
};

const BucketRow = ({
  bucket,
  selected,
  onSelect
}: {
  bucket: DiscoveredBucket;
  selected: boolean;
  onSelect: () => void;
}) => (
  <button
    type="button"
    onClick={onSelect}
    aria-pressed={selected}
    className={cn(
      "flex w-full items-center gap-2 rounded-[var(--radius-default)] border px-2.5 py-1.5 text-left transition-colors duration-[var(--motion-fast)]",
      selected
        ? "border-foreground bg-[var(--color-surface-sunken)]"
        : "border-border hover:bg-[var(--color-surface-sunken)]"
    )}
  >
    <Database className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
    <span className="min-w-0 flex-1 truncate text-xs font-medium">{bucket.name}</span>
    <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
      {bucket.tables.toLocaleString()} table{bucket.tables === 1 ? "" : "s"}
    </span>
    <span className="shrink-0 text-xs">
      <AccessBadge bucket={bucket} />
    </span>
  </button>
);

/**
 * Buckets the catalog's tables live in, ranked by how much they hold and labelled with
 * whether Cryo can actually read them. The walk streams, so rows appear as slices land
 * rather than after the whole multi-minute sweep.
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
  const [showAll, setShowAll] = useState(false);
  const { status, buckets, scanned, total, warnings, error } = state;

  if (status === "idle") return null;

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
  const totalTables = buckets.reduce((sum, b) => sum + b.tables, 0);

  // Split on share of tables, not a fixed count, so a catalog that genuinely uses twenty
  // buckets evenly shows all twenty rather than hiding half of them.
  const significant = buckets.filter(
    (b) => totalTables === 0 || b.tables / totalTables >= SIGNIFICANT_SHARE
  );
  const minor = buckets.filter((b) => !significant.includes(b));
  const visible = showAll ? buckets : significant;

  // Only worth warning about once the walk has settled -- mid-walk, an unreadable bucket
  // may simply not have been probed yet.
  const blockedTables = !running
    ? buckets.filter((b) => b.access === "denied" || b.access === "missing")
    : [];
  const blockedShare =
    totalTables > 0 ? blockedTables.reduce((sum, b) => sum + b.tables, 0) / totalTables : 0;

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
          ? "Reading where each schema's tables are stored. Buckets appear as they're found — pick one without waiting for the rest."
          : buckets.length > 0
            ? "Ordered by how many tables each holds. Selecting one records it on this lakehouse; Cryo still authenticates with the method below."
            : `Checked ${total} schema${total === 1 ? "" : "s"} — none record an s3:// (or gs://, abfss://) location. Normal for a local or HDFS warehouse; fill in the fields below manually.`}
      </p>

      {visible.length > 0 ? (
        <div className="mt-2 flex flex-col gap-1">
          {visible.map((bucket) => (
            <BucketRow
              key={bucket.name}
              bucket={bucket}
              selected={selectedBucket === bucket.name}
              onSelect={() => onSelect(bucket.name)}
            />
          ))}
        </div>
      ) : null}

      {!showAll && minor.length > 0 ? (
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="mt-1 h-auto p-0 text-xs"
          onClick={() => setShowAll(true)}
        >
          Show {minor.length} smaller bucket{minor.length === 1 ? "" : "s"}
        </Button>
      ) : null}

      {/* The case that actually bites: onboarding looks fine, then every scan fails on
          AccessDenied. Say it here, while the credentials fields are still on screen. */}
      {blockedTables.length > 0 ? (
        <Alert
          className="mt-2"
          variant={blockedShare >= 0.5 ? "warning" : "info"}
          title={
            blockedShare >= 0.5
              ? "Cryo can't read most of your tables"
              : `Cryo can't read ${blockedTables.length} bucket${blockedTables.length === 1 ? "" : "s"}`
          }
          description={
            <>
              {blockedTables
                .slice(0, 3)
                .map((b) => b.name)
                .join(", ")}
              {blockedTables.length > 3 ? ` and ${blockedTables.length - 3} more` : ""}. Scans will
              fail for those tables until the credentials below (or the IAM role behind them) are
              granted read access.
            </>
          }
        />
      ) : null}

      {!running && warnings.length > 0 ? (
        <p className="mt-1.5 flex items-center gap-1 text-xs text-muted-foreground">
          <AlertTriangle className="size-3" aria-hidden="true" />
          {warnings.length} schema group{warnings.length === 1 ? "" : "s"} couldn&apos;t be read, so
          this list may be incomplete.
        </p>
      ) : null}
    </div>
  );
};
