import type { ColumnDef } from "@tanstack/react-table";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, Copy, Crosshair, GitBranch, History, Tag } from "lucide-react";
import { useMemo } from "react";
import { toast } from "sonner";
import { ConfirmAction } from "../../components/confirm";
import { Popover, PopoverContent, PopoverTrigger } from "../../components/ui/popover";
import { tableKeys } from "./tableKeys";
import "./snapshotsTab.css";
import { useParams, useSearchParams } from "react-router-dom";
import {
  listProtectedSnapshots,
  listTableSnapshots,
  protectTableSnapshot,
  rollbackTableSnapshot,
  type SnapshotRef,
  type TableSnapshot,
  type TableSnapshotsResponse
} from "../../api/snapshots";
import { QueryErrorAlert } from "../../components/common";
import { ExcludedFromExpiry } from "../../components/table-detail/ExcludedFromExpiry";
import { useOperationLauncher } from "../../components/table-detail/operations/OperationLauncher";
import { SnapshotSelection, writerLabel } from "../../components/table-detail/SnapshotSelection";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card } from "../../components/ui/card";
import { DataTable } from "../../components/ui/data-table";
import { useAuth } from "../../lib/auth";
import { useWriteControl } from "../../lib/useWriteControl";
import { formatBytesGb, formatDuration, formatNumber, formatTime } from "../../lib/format";

const PAGE_SIZE = 25;

const pageFromUrl = (raw: string | null): number => {
  const parsed = Number(raw);
  return Number.isInteger(parsed) && parsed >= 1 ? parsed : 1;
};

const formatRelative = (value?: string | null): string => {
  if (!value) return "Unknown";
  const ts = new Date(value).getTime();
  if (Number.isNaN(ts)) return "Unknown";
  const deltaSec = Math.round((Date.now() - ts) / 1000);
  const abs = Math.abs(deltaSec);
  const suffix = deltaSec >= 0 ? "ago" : "from now";
  if (abs < 60) return deltaSec >= 0 ? "just now" : "in under a minute";
  const minutes = Math.round(abs / 60);
  if (minutes < 60) return `${minutes}m ${suffix}`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ${suffix}`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days}d ${suffix}`;
  return formatTime(value);
};

const sizeLabel = (bytes: number | null | undefined): string => {
  if (bytes === null || bytes === undefined) return "Unknown";
  return formatBytesGb(bytes);
};

const sameSnapshotId = (left: string | null | undefined, right: string | null | undefined): boolean =>
  typeof left === "string" && left === right;

const retentionTitle = (ref: SnapshotRef): string | undefined => {
  const parts: string[] = [];
  if (ref.max_ref_age_ms != null) parts.push(`max ref age ${formatDuration(ref.max_ref_age_ms)}`);
  if (ref.max_snapshot_age_ms != null) {
    parts.push(`max snapshot age ${formatDuration(ref.max_snapshot_age_ms)}`);
  }
  if (ref.min_snapshots_to_keep != null) parts.push(`keep ${ref.min_snapshots_to_keep} snapshots`);
  return parts.length > 0 ? parts.join(", ") : undefined;
};

const When = ({ value }: { value?: string | null }) => {
  if (!value) return <span>Unknown</span>;
  return (
    <time dateTime={value} title={formatTime(value)}>
      {formatRelative(value)}
    </time>
  );
};

const SummaryCard = ({
  total,
  pinned,
  excluded,
  latest,
  oldest
}: {
  total: number;
  pinned: number;
  excluded: number | null;
  latest: string | null;
  oldest: string | null;
}) => {
  const launcher = useOperationLauncher();
  return (
    <Card className="td-snapshots-summary">
      <div className="td-snapshots-summary-heading">
        <div><h2>Snapshot history</h2><p>See what changed, compare recorded summaries, and manage retention.</p></div>
        {launcher ? <Button type="button" size="sm" variant="secondary" disabled={Boolean(launcher.blockedReason)} title={launcher.blockedReason ?? "Queues snapshot expiry. It does not list the rows that will be removed."} onClick={() => launcher.launch("expire_snapshots")}>Expire snapshots…</Button> : null}
      </div>
      <dl className="td-snapshots-facts">
        <div><dt>Live snapshots</dt><dd>{formatNumber(total)}</dd></div>
        <div><dt>Referenced by tags or branches</dt><dd>{formatNumber(pinned)}</dd></div>
        <div><dt>Excluded from Cryo expiry</dt><dd>{excluded == null ? "—" : formatNumber(excluded)}</dd></div>
        <div><dt>Latest commit</dt><dd><When value={latest} /></dd></div>
        <div><dt>Oldest retained commit</dt><dd><When value={oldest} /></dd></div>
      </dl>
    </Card>
  );
};

const signed = (value: number, sign: "+" | "−") => `${sign}${formatNumber(value)}`;

/** Rows and delete files this commit added or removed, from the writer's snapshot summary. */
const CommitChange = ({ summary }: { summary: TableSnapshot["summary"] }) => {
  const rows = [
    summary.added_records ? signed(summary.added_records, "+") : null,
    summary.deleted_records ? signed(summary.deleted_records, "−") : null
  ].filter(Boolean);
  const deletes = summary.added_delete_files ?? 0;
  if (!rows.length && !deletes) {
    const recorded = summary.added_records != null || summary.deleted_records != null;
    return <span className="text-muted-foreground">{recorded ? "No row change" : "—"}</span>;
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-1 whitespace-nowrap">
      {rows.length ? <span>{rows.join(" ")} rows</span> : null}
      {deletes ? (
        <Badge variant="warning" title="Merge-on-read delete files. Readers apply them until compaction rewrites the data.">
          +{formatNumber(deletes)} delete file{deletes === 1 ? "" : "s"}
        </Badge>
      ) : null}
    </span>
  );
};

const shortId = (id: string): string => (id.length > 10 ? `…${id.slice(-8)}` : id);

const SnapshotActions = ({
  snapshotId,
  committedAt,
  alreadyExcluded,
  isCurrent,
  protectAllowed,
  protectReason,
  pending,
  onProtect,
  rollbackAllowed,
  rollbackReason,
  rollbackPending,
  onRollback
}: {
  snapshotId: string;
  committedAt: string | null;
  alreadyExcluded: boolean;
  isCurrent: boolean;
  protectAllowed: boolean;
  protectReason: string | null;
  pending: boolean;
  onProtect: (snapshotId: string) => void;
  rollbackAllowed: boolean;
  rollbackReason: string | null;
  rollbackPending: boolean;
  onRollback: (snapshotId: string) => void;
}) => {
  return (
    <Popover>
      <PopoverTrigger asChild><Button type="button" size="sm" variant="ghost">Actions</Button></PopoverTrigger>
      <PopoverContent align="end" className="w-72 p-3">
        <div role="group" aria-label="Snapshot actions" className="flex flex-col gap-1">
          <Button type="button" size="sm" variant="secondary" className="w-full justify-start" disabled={!protectAllowed || pending || alreadyExcluded} title={protectReason ?? undefined} onClick={() => onProtect(snapshotId)}>
            {alreadyExcluded ? "Already excluded" : "Protect"}
          </Button>
          <p className="text-xs text-muted-foreground">
            Records snapshot {snapshotId} so a later Spark expire keeps it. It does not follow the branch.
            {isCurrent ? " This is the current commit, not later commits." : ""} Trino expire cannot skip one id.
          </p>
          {protectReason ? <p className="text-xs text-muted-foreground">{protectReason}</p> : null}
          <div className="mt-2 border-t pt-2">
            <ConfirmAction
              title="Roll back to this snapshot?"
              description={
                <>
                  Moves the table's current pointer to snapshot {snapshotId}
                  {committedAt ? ` (committed ${formatTime(committedAt)})` : ""}. Anything committed after it stops
                  being current for every reader, not just Cryo. Data files stay on disk until a later expire run.
                </>
              }
              okText="Roll back"
              okButtonProps={{ danger: true, loading: rollbackPending }}
              onConfirm={() => onRollback(snapshotId)}
              disabled={!rollbackAllowed || rollbackPending || isCurrent}
            >
              <Button
                type="button"
                size="sm"
                variant="secondary"
                className="w-full justify-start"
                disabled={!rollbackAllowed || rollbackPending || isCurrent}
                title={isCurrent ? "This is already the current snapshot" : (rollbackReason ?? undefined)}
              >
                <History size={14} aria-hidden="true" /> {isCurrent ? "Current snapshot" : "Apply (roll back)"}
              </Button>
            </ConfirmAction>
            {rollbackReason && !isCurrent ? <p className="mt-2 text-xs text-muted-foreground">{rollbackReason}</p> : null}
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
};

export const SnapshotsTab = () => {
  const { tableId = "" } = useParams();
  const { api, token } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const page = pageFromUrl(searchParams.get("page"));
  const snapshots = useQuery({
    queryKey: ["table-snapshots", tableId, api.token, page],
    queryFn: () => listTableSnapshots(api, tableId, { page, page_size: PAGE_SIZE }),
    enabled: tableId.length > 0
  });

  const protectAccess = useWriteControl("protectSnapshot");
  const excluded = useQuery({
    queryKey: tableKeys.protectedSnapshots(tableId, token),
    queryFn: () => listProtectedSnapshots(api, tableId),
    enabled: tableId.length > 0
  });
  const protect = useMutation({
    mutationFn: (snapshotId: string) => protectTableSnapshot(api, tableId, snapshotId),
    onSuccess: (_result, snapshotId) => {
      toast.success(`Snapshot ${snapshotId} recorded for Cryo expiry exclusion`);
      queryClient.setQueriesData<TableSnapshotsResponse>({ queryKey: ["table-snapshots", tableId] }, (current) => {
        if (!current) return current;
        return {
          ...current,
          snapshots: current.snapshots.map((row) =>
            row.snapshot_id === snapshotId ? { ...row, protected: true } : row
          )
        };
      });
      void queryClient.invalidateQueries({ queryKey: ["table-snapshots", tableId] });
      void queryClient.invalidateQueries({ queryKey: tableKeys.protectedSnapshots(tableId, token) });
    },
    onError: (error: Error) => toast.error(error.message)
  });

  const rollbackAccess = useWriteControl("rollbackSnapshot");
  const rollback = useMutation({
    mutationFn: (snapshotId: string) => rollbackTableSnapshot(api, tableId, snapshotId),
    onSuccess: (result) => {
      toast.success(`Table rolled back to snapshot ${result.snapshot_id}`);
      void queryClient.invalidateQueries({ queryKey: ["table-snapshots", tableId] });
      void queryClient.invalidateQueries({ queryKey: tableKeys.detail(tableId, token) });
    },
    onError: (error: Error) => toast.error(error.message)
  });

  const setPage = (pageIndex: number) => {
    const next = new URLSearchParams(searchParams);
    const pageNumber = pageIndex + 1;
    if (pageNumber <= 1) next.delete("page");
    else next.set("page", String(pageNumber));
    setSearchParams(next, { replace: true });
  };

  const data = snapshots.data;
  const loadedIds = useMemo(
    () => new Set((data?.snapshots ?? []).map((row) => row.snapshot_id)),
    [data?.snapshots]
  );
  const currentSnapshotId = data?.current_snapshot_id ?? null;
  const columns = useMemo<ColumnDef<TableSnapshot, unknown>[]>(
    () => [
      {
        id: "snapshot_id",
        header: "Snapshot",
        cell: ({ row }) => (
          <span className="inline-flex items-center gap-0.5 whitespace-nowrap">
            <span className="flex flex-col">
              <span data-snapshot-id={row.original.snapshot_id} title={row.original.snapshot_id} className="font-mono text-xs">
                {shortId(row.original.snapshot_id)}
              </span>
              <span
                data-parent-snapshot-id={row.original.parent_snapshot_id ?? ""}
                title={row.original.parent_snapshot_id ?? undefined}
                className="font-mono text-[11px] text-muted-foreground"
              >
                {row.original.parent_snapshot_id
                  ? `from ${loadedIds.has(row.original.parent_snapshot_id) ? shortId(row.original.parent_snapshot_id) : "outside this page"}`
                  : "first snapshot"}
              </span>
            </span>
            <Button
              type="button"
              size="icon"
              variant="ghost"
              className="size-7"
              aria-label={`Copy snapshot ${row.original.snapshot_id}`}
              title="Copy full snapshot id"
              onClick={() => {
                if (navigator.clipboard) void navigator.clipboard.writeText(row.original.snapshot_id);
              }}
            >
              <Copy className="!size-3.5" aria-hidden="true" />
            </Button>
            <Button
              type="button"
              size="icon"
              variant="ghost"
              className="size-7"
              aria-label={`Select snapshot ${row.original.snapshot_id}`}
              title="Select for comparison"
              onClick={() => {
                const next = new URLSearchParams(searchParams);
                next.set("snapshot", row.original.snapshot_id);
                setSearchParams(next, { replace: true });
              }}
            >
              <Crosshair className="!size-3.5" aria-hidden="true" />
            </Button>
          </span>
        )
      },
      {
        id: "timestamp",
        header: "Timestamp",
        cell: ({ row }) => (
          <span className="inline-flex items-center gap-2 whitespace-nowrap">
            <span
              className="inline-block size-1.5 rounded-full bg-[var(--color-primary)]"
              aria-hidden="true"
            />
            <time
              dateTime={row.original.committed_at ?? undefined}
              title={row.original.sequence_number == null ? undefined : `Sequence ${formatNumber(row.original.sequence_number)}`}
            >
              {formatTime(row.original.committed_at)}
            </time>
            {sameSnapshotId(row.original.snapshot_id, currentSnapshotId) ? <Badge>Current</Badge> : null}
          </span>
        )
      },
      {
        id: "operation",
        header: "Operation",
        cell: ({ row }) => row.original.operation ?? "Unknown"
      },
      {
        id: "change",
        header: "Change",
        cell: ({ row }) => <CommitChange summary={row.original.summary} />
      },
      {
        id: "partitions",
        header: "Partitions",
        cell: ({ row }) =>
          row.original.summary.changed_partition_count == null ? (
            <span className="text-muted-foreground" title="Not recorded by this writer">—</span>
          ) : (
            formatNumber(row.original.summary.changed_partition_count)
          )
      },
      {
        id: "table_after",
        header: "Table after",
        cell: ({ row }) => (
          <span className="whitespace-nowrap" title={`${formatNumber(row.original.summary.total_records)} rows`}>
            {formatNumber(row.original.summary.total_records, true)} rows · {sizeLabel(row.original.summary.total_files_size)}
          </span>
        )
      },
      {
        id: "writer",
        header: "Writer",
        cell: ({ row }) => {
          const writer = row.original.summary.writer;
          const label = writerLabel(writer);
          return label ? (
            <span className="whitespace-nowrap" title={[writer?.user, writer?.query_id ?? writer?.job_id].filter(Boolean).join(" · ") || undefined}>
              {label}
            </span>
          ) : (
            <span className="text-muted-foreground" title="Not recorded by this writer">—</span>
          );
        }
      },
      {
        id: "retention",
        header: "Retention",
        cell: ({ row }) =>
          row.original.refs.length === 0 && !row.original.protected ? (
            <span className="text-muted-foreground">—</span>
          ) : (
            <span className="flex flex-wrap gap-1">
              {row.original.refs.map((ref) => (
                <span
                  key={`${ref.type}:${ref.name}`}
                  title={retentionTitle(ref)}
                  className="inline-flex items-center gap-1 rounded-full bg-[var(--color-primary-soft)] px-2 py-0.5 text-xs text-[var(--color-primary)]"
                >
                  {ref.type === "tag" ? (
                    <Tag className="size-3" aria-hidden="true" />
                  ) : (
                    <GitBranch className="size-3" aria-hidden="true" />
                  )}
                  {ref.name}
                </span>
              ))}
              {row.original.protected ? (
                <Badge variant="warning" title={row.original.protected_label ?? undefined}>
                  Excluded from Cryo expiry
                </Badge>
              ) : null}
            </span>
          )
      },
      {
        id: "actions",
        header: "",
        cell: ({ row }) => (
          <SnapshotActions
            snapshotId={row.original.snapshot_id}
            committedAt={row.original.committed_at}
            alreadyExcluded={row.original.protected}
            isCurrent={row.original.snapshot_id === currentSnapshotId}
            protectAllowed={protectAccess.allowed}
            protectReason={protectAccess.reason}
            pending={protect.isPending && protect.variables === row.original.snapshot_id}
            onProtect={(snapshotId) => protect.mutate(snapshotId)}
            rollbackAllowed={rollbackAccess.allowed}
            rollbackReason={rollbackAccess.reason}
            rollbackPending={rollback.isPending && rollback.variables === row.original.snapshot_id}
            onRollback={(snapshotId) => rollback.mutate(snapshotId)}
          />
        )
      }
    ],
    [
      currentSnapshotId,
      loadedIds,
      protect,
      protectAccess.allowed,
      protectAccess.reason,
      rollback,
      rollbackAccess.allowed,
      rollbackAccess.reason,
      searchParams,
      setSearchParams
    ]
  );

  return (
    <div className="td-snapshots-workspace">
      <QueryErrorAlert error={snapshots.error} title="Couldn't read snapshot metadata" />
      {data ? (
        <SummaryCard
          total={data.total}
          pinned={data.pinned_count}
          excluded={excluded.isError ? null : excluded.data ? excluded.data.snapshots.length : null}
          latest={data.latest_committed_at}
          oldest={data.oldest_committed_at}
        />
      ) : null}
      <Card className="td-snapshots-retention">
        <ExcludedFromExpiry tableId={tableId} />
        <p className="text-xs text-muted-foreground">
          Tags and branches follow Iceberg's own retention rules. An expiry job that is already planning won't see a change
          made after it started.
        </p>
      </Card>
      <SnapshotSelection
        snapshots={data?.snapshots ?? []}
        selectedId={searchParams.get("snapshot")}
        compareRaw={searchParams.get("compare")}
      />
      <section className="td-snapshots-history" aria-labelledby="snapshot-history-title">
      <div className="td-snapshots-section-heading"><h3 id="snapshot-history-title">Commits</h3><p>Each row is a retained table version. Select a snapshot to inspect or compare its summary.</p></div>
      {!snapshots.isError || data ? <DataTable
        columns={columns}
        data={data?.snapshots ?? []}
        loading={snapshots.isLoading}
        density="compact"
        getRowId={(row) => row.snapshot_id}
        emptyTitle="No live snapshots"
        emptyDescription={data && data.total > 0 ? "No snapshots on this page. Return to the first page to browse retained history." : "Catalog metadata has no snapshots for this table."}
        emptyAction={data && data.total > 0 ? <Button size="sm" variant="secondary" onClick={() => setPage(0)}>First page</Button> : undefined}
        pagination={
          data
            ? {
                pageIndex: page - 1,
                pageSize: PAGE_SIZE,
                totalRows: data.total,
                onPageChange: setPage
              }
            : undefined
        }
      /> : null}
      </section>
    </div>
  );
};
