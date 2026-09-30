import type { ColumnDef } from "@tanstack/react-table";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, Copy, Crosshair, GitBranch, Tag } from "lucide-react";
import { useMemo } from "react";
import { toast } from "sonner";
import { Popover, PopoverContent, PopoverTrigger } from "../../components/ui/popover";
import { tableKeys } from "./tableKeys";
import "./snapshotsTab.css";
import { useParams, useSearchParams } from "react-router-dom";
import {
  listTableSnapshots,
  protectTableSnapshot,
  type SnapshotRef,
  type TableSnapshot
} from "../../api/snapshots";
import { QueryErrorAlert } from "../../components/common";
import { ExcludedFromExpiry } from "../../components/table-detail/ExcludedFromExpiry";
import { useOperationLauncher } from "../../components/table-detail/operations/OperationLauncher";
import { SnapshotSelection } from "../../components/table-detail/SnapshotSelection";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card } from "../../components/ui/card";
import { DataTable } from "../../components/ui/data-table";
import { useAuth } from "../../lib/auth";
import { useWriteControl } from "../../lib/useWriteControl";
import { formatBytesGb, formatDuration, formatNumber, formatTime } from "../../lib/format";

const PAGE_SIZE = 25;
const RESTORE_REASON = "Restore is not enabled on this install";

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
  latest,
  oldest
}: {
  total: number;
  pinned: number;
  latest: string | null;
  oldest: string | null;
}) => {
  const launcher = useOperationLauncher();
  return (
    <Card className="td-snapshots-summary">
      <div className="td-snapshots-summary-heading">
        <div><h2>Snapshot history</h2><p>See what changed, compare recorded summaries, and manage retention.</p></div>
        {launcher ? <Button type="button" size="sm" variant="secondary" disabled={Boolean(launcher.blockedReason)} title={launcher.blockedReason ?? undefined} onClick={() => launcher.launch("expire_snapshots")}><Archive aria-hidden="true" />Preview expiry</Button> : null}
      </div>
      <dl className="td-snapshots-facts">
        <div><dt>Live snapshots</dt><dd>{formatNumber(total)}</dd></div>
        <div><dt>Referenced by tags or branches</dt><dd>{formatNumber(pinned)}</dd></div>
        <div><dt>Latest commit</dt><dd><When value={latest} /></dd></div>
        <div><dt>Oldest retained commit</dt><dd><When value={oldest} /></dd></div>
      </dl>
    </Card>
  );
};

const shortId = (id: string): string => (id.length > 10 ? `…${id.slice(-8)}` : id);

const SnapshotActions = ({
  snapshotId,
  protectAllowed,
  protectReason,
  pending,
  onProtect
}: {
  snapshotId: string;
  protectAllowed: boolean;
  protectReason: string | null;
  pending: boolean;
  onProtect: (snapshotId: string) => void;
}) => {
  return (
    <Popover>
      <PopoverTrigger asChild><Button type="button" size="sm" variant="ghost">Actions</Button></PopoverTrigger>
      <PopoverContent align="end" className="w-72 p-3">
        <div role="group" aria-label="Snapshot actions">
          <Button type="button" size="sm" variant="secondary" className="w-full justify-start" disabled={!protectAllowed || pending} title={protectReason ?? undefined} onClick={() => onProtect(snapshotId)}>Protect</Button>
          {protectReason ? <p className="mt-2 text-xs text-muted-foreground">{protectReason}</p> : null}
          <p className="mt-3 text-xs text-muted-foreground">{RESTORE_REASON}.</p>
        </div>
      </PopoverContent>
    </Popover>
  );
};

export const SnapshotsTab = () => {
  const { tableId = "" } = useParams();
  const { api } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const page = pageFromUrl(searchParams.get("page"));
  const snapshots = useQuery({
    queryKey: ["table-snapshots", tableId, api.token, page],
    queryFn: () => listTableSnapshots(api, tableId, { page, page_size: PAGE_SIZE }),
    enabled: tableId.length > 0
  });

  const protectAccess = useWriteControl("protectSnapshot");
  const protect = useMutation({
    mutationFn: (snapshotId: string) => protectTableSnapshot(api, tableId, snapshotId),
    onSuccess: () => {
      toast.success("Snapshot excluded from Cryo expiry");
      void queryClient.invalidateQueries({ queryKey: ["table-snapshots", tableId] });
      void queryClient.invalidateQueries({ queryKey: tableKeys.protectedSnapshots(tableId, api.token) });
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
            <span data-snapshot-id={row.original.snapshot_id} title={row.original.snapshot_id} className="font-mono text-xs">
              {shortId(row.original.snapshot_id)}
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
        id: "parent_snapshot_id",
        header: "Parent",
        cell: ({ row }) => (
          <span
            data-parent-snapshot-id={row.original.parent_snapshot_id ?? ""}
            title={row.original.parent_snapshot_id ?? undefined}
            className="whitespace-nowrap font-mono text-xs"
          >
            {row.original.parent_snapshot_id
              ? loadedIds.has(row.original.parent_snapshot_id)
                ? shortId(row.original.parent_snapshot_id)
                : "Outside loaded page"
              : "—"}
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
            <time dateTime={row.original.committed_at ?? undefined} title={formatTime(row.original.committed_at)}>
              {formatTime(row.original.committed_at)}
            </time>
            {sameSnapshotId(row.original.snapshot_id, currentSnapshotId) ? <Badge>Current</Badge> : null}
          </span>
        )
      },
      {
        id: "sequence",
        header: "Sequence",
        cell: ({ row }) => formatNumber(row.original.sequence_number)
      },
      {
        id: "operation",
        header: "Operation",
        cell: ({ row }) => row.original.operation ?? "Unknown"
      },
      {
        id: "records",
        header: "Records",
        cell: ({ row }) => formatNumber(row.original.summary.total_records)
      },
      {
        id: "size",
        header: "Total size",
        cell: ({ row }) => <span className="whitespace-nowrap">{sizeLabel(row.original.summary.total_files_size)}</span>
      },
      {
        id: "refs",
        header: "References",
        cell: ({ row }) =>
          row.original.refs.length === 0 ? (
            <span className="text-muted-foreground">None</span>
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
            </span>
          )
      },
      {
        id: "protected",
        header: "Protected",
        cell: ({ row }) =>
          row.original.protected ? (
            <Badge variant="warning" title={row.original.protected_label ?? undefined}>
              Excluded from Cryo expiry
            </Badge>
          ) : (
            <span className="text-muted-foreground">No</span>
          )
      },
      {
        id: "actions",
        header: "",
        cell: ({ row }) => (
          <SnapshotActions
            snapshotId={row.original.snapshot_id}
            protectAllowed={protectAccess.allowed}
            protectReason={protectAccess.reason}
            pending={protect.isPending}
            onProtect={(snapshotId) => protect.mutate(snapshotId)}
          />
        )
      }
    ],
    [currentSnapshotId, loadedIds, protect, protectAccess.allowed, protectAccess.reason, searchParams, setSearchParams]
  );

  return (
    <div className="td-snapshots-workspace">
      <QueryErrorAlert error={snapshots.error} title="Couldn't read snapshot metadata" />
      {data ? (
        <SummaryCard
          total={data.total}
          pinned={data.pinned_count}
          latest={data.latest_committed_at}
          oldest={data.oldest_committed_at}
        />
      ) : null}
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
      <Card className="td-snapshots-retention"><ExcludedFromExpiry tableId={tableId} /><p className="text-xs text-muted-foreground">Tags and branches follow Iceberg retention rules. Cryo exclusions only affect Cryo expiry. Preview an expiry plan before deciding what to remove.</p></Card>
    </div>
  );
};
