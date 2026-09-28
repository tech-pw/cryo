import type { ColumnDef } from "@tanstack/react-table";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { GitBranch, Tag } from "lucide-react";
import { useMemo, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { protectSnapshot } from "../../api/client";
import { listTableSnapshots, type SnapshotRef, type TableSnapshot } from "../../api/snapshots";
import { QueryErrorAlert } from "../../components/common";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card } from "../../components/ui/card";
import { DataTable } from "../../components/ui/data-table";
import { useAuth } from "../../lib/auth";
import { formatBytesGb, formatDuration, formatNumber, formatTime } from "../../lib/format";
import { writeAccess } from "../../lib/permissions";

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
}) => (
  <Card className="mb-4">
    <div className="flex items-end gap-6 p-4">
      <div>
        <p className="text-xs text-muted-foreground">Live snapshots</p>
        <p className="text-2xl font-semibold text-foreground [font-variant-numeric:tabular-nums]">{total}</p>
      </div>
      <div className="h-10 w-px bg-border" aria-hidden="true" />
      <div>
        <p className="text-xs text-muted-foreground">Pinned by tags or branches</p>
        <p className="text-2xl font-semibold text-foreground [font-variant-numeric:tabular-nums]">{pinned}</p>
      </div>
    </div>
    <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-border bg-[var(--color-surface-sunken)] px-4 py-2 text-xs text-muted-foreground">
      <span>
        Latest <When value={latest} />
      </span>
      <span>
        Oldest <When value={oldest} />
      </span>
    </div>
  </Card>
);

const SnapshotActions = ({
  snapshotId,
  protectAllowed,
  protectReason,
  pending,
  onProtect
}: {
  snapshotId: number;
  protectAllowed: boolean;
  protectReason: string | null;
  pending: boolean;
  onProtect: (snapshotId: number) => void;
}) => {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <Button
        type="button"
        size="sm"
        variant="ghost"
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen((current) => !current)}
      >
        Actions
      </Button>
      {open ? (
        <div
          role="menu"
          className="absolute right-0 z-20 mt-1 w-72 rounded-[var(--radius-default)] border border-border bg-card p-2 shadow-[var(--shadow-card)]"
        >
          <Button
            type="button"
            role="menuitem"
            size="sm"
            variant="ghost"
            className="w-full justify-start"
            disabled={!protectAllowed || pending}
            title={protectReason ?? undefined}
            onClick={() => onProtect(snapshotId)}
          >
            Protect
          </Button>
          {protectReason ? <p className="px-3 pb-2 text-xs text-muted-foreground">{protectReason}</p> : null}
          <Button
            type="button"
            role="menuitem"
            size="sm"
            variant="ghost"
            className="w-full justify-start"
            disabled
            title={RESTORE_REASON}
          >
            Restore…
          </Button>
          <p className="px-3 pb-1 text-xs text-muted-foreground">{RESTORE_REASON}</p>
        </div>
      ) : null}
    </div>
  );
};

export const SnapshotsTab = () => {
  const { tableId = "" } = useParams();
  const { api, role } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const page = pageFromUrl(searchParams.get("page"));
  const protectAccess = writeAccess(role, "protectSnapshot");

  const snapshots = useQuery({
    queryKey: ["table-snapshots", tableId, api.token, page],
    queryFn: () => listTableSnapshots(api, tableId, { page, page_size: PAGE_SIZE }),
    enabled: tableId.length > 0
  });

  const protect = useMutation({
    mutationFn: (snapshotId: number) => protectSnapshot(api, tableId, { snapshot_id: snapshotId }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["table-snapshots", tableId] });
    }
  });

  const setPage = (pageIndex: number) => {
    const next = new URLSearchParams(searchParams);
    const pageNumber = pageIndex + 1;
    if (pageNumber <= 1) next.delete("page");
    else next.set("page", String(pageNumber));
    setSearchParams(next, { replace: true });
  };

  const columns = useMemo<ColumnDef<TableSnapshot, unknown>[]>(
    () => [
      {
        id: "timestamp",
        header: "Timestamp",
        cell: ({ row }) => (
          <span className="inline-flex items-center gap-2">
            <span
              className="inline-block size-1.5 rounded-full bg-[var(--color-primary)]"
              aria-hidden="true"
            />
            <time dateTime={row.original.committed_at ?? undefined} title={formatTime(row.original.committed_at)}>
              {formatTime(row.original.committed_at)}
            </time>
            {row.original.is_current ? <Badge>Current</Badge> : null}
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
        cell: ({ row }) => sizeLabel(row.original.summary.total_files_size)
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
              Protected
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
    [protect, protectAccess.allowed, protectAccess.reason]
  );

  const data = snapshots.data;

  return (
    <div>
      <QueryErrorAlert error={snapshots.error} title="Couldn't read snapshot metadata" />
      {data ? (
        <SummaryCard
          total={data.total}
          pinned={data.pinned_count}
          latest={data.latest_committed_at}
          oldest={data.oldest_committed_at}
        />
      ) : null}
      <DataTable
        columns={columns}
        data={data?.snapshots ?? []}
        loading={snapshots.isLoading}
        density="compact"
        getRowId={(row) => String(row.snapshot_id)}
        emptyTitle="No live snapshots"
        emptyDescription="Catalog metadata has no snapshots for this table."
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
      />
    </div>
  );
};
