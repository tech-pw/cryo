import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import {
  getSharedS3Anomalies,
  refreshSharedS3Anomalies,
  type SharedS3Group,
  type SharedS3Report
} from "../api/client";
import { AsyncBoundary } from "../components/AsyncBoundary";
import { PageHeader, QueryErrorAlert } from "../components/common";
import { Alert } from "../components/ui/alert";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { DataTable } from "../components/ui/data-table";
import { MetricGrid, MetricTile } from "../components/ui/metric-grid";
import { useAuth } from "../lib/auth";
import { formatTime } from "../lib/format";

const PAGE_SIZE = 25;

export const Anomalies = () => {
  const { api } = useAuth();
  const queryClient = useQueryClient();
  const [pageIndex, setPageIndex] = useState(0);

  const reportQuery = useQuery({
    queryKey: ["shared-s3-anomalies", api.token],
    queryFn: () => getSharedS3Anomalies(api)
  });

  const refresh = useMutation({
    mutationFn: () => refreshSharedS3Anomalies(api),
    onSuccess: (data) => {
      void queryClient.setQueryData(["shared-s3-anomalies", api.token], data);
      toast.success(
        data.overlapping_group_count
          ? `Found ${data.overlapping_group_count} shared S3 path group${data.overlapping_group_count === 1 ? "" : "s"}`
          : "No shared S3 paths found"
      );
    },
    onError: (error: unknown) => {
      toast.error(error instanceof Error ? error.message : "Harvest failed");
    }
  });

  const report: SharedS3Report | undefined = reportQuery.data;
  const groups = report?.groups ?? [];
  const fallbackSources = (report?.sources ?? []).filter((source) => source.mode === "db_fallback" || source.error);
  const pageGroups = groups.slice(pageIndex * PAGE_SIZE, (pageIndex + 1) * PAGE_SIZE);

  const columns = useMemo<ColumnDef<SharedS3Group>[]>(
    () => [
      {
        accessorKey: "shared_path",
        header: "Shared S3 path",
        cell: ({ row }) => (
          <code className="break-all text-xs">{row.original.shared_path}</code>
        )
      },
      {
        accessorKey: "kind",
        header: "Kind",
        cell: ({ row }) => (
          <Badge variant={row.original.kind === "exact" ? "danger" : "warning"}>
            {row.original.kind === "exact" ? "Exact" : "Nested"}
          </Badge>
        )
      },
      {
        accessorKey: "table_count",
        header: "Tables",
        cell: ({ row }) => row.original.table_count
      },
      {
        id: "members",
        header: "Catalog names",
        cell: ({ row }) => (
          <ul className="space-y-1">
            {row.original.tables.map((member) => (
              <li key={`${member.source_id}.${member.schema_name}.${member.table_name}`}>
                {member.table_id ? (
                  <Link className="text-sm text-[var(--color-primary)] hover:underline" to={`/tables/${member.table_id}`}>
                    {member.qualified_name}
                  </Link>
                ) : (
                  <span className="text-sm">{member.qualified_name}</span>
                )}
              </li>
            ))}
          </ul>
        )
      }
    ],
    []
  );

  const hasSnapshot = Boolean(report?.generated_at);
  const isClear = hasSnapshot && groups.length === 0;

  return (
    <div>
      <PageHeader
        title="Anomalies"
        subtitle="Catalog names that share an S3 warehouse — or nest under one — are one physical table. Dropping an alias deletes the sibling."
        actions={
          <Button
            variant="primary"
            onClick={() => refresh.mutate()}
            disabled={refresh.isPending}
          >
            <RefreshCw className={refresh.isPending ? "animate-spin" : undefined} />
            {refresh.isPending ? "Harvesting catalogs…" : "Refresh from catalogs"}
          </Button>
        }
      />

      {reportQuery.isError ? <QueryErrorAlert error={reportQuery.error} /> : null}

      {fallbackSources.length > 0 ? (
        <Alert
          className="mb-4"
          variant="warning"
          title="Some catalogs used scanned locations instead of live metastore metadata"
          description={fallbackSources
            .map(
              (source) =>
                `${source.source_name} (${source.mode}${source.error ? `: ${source.error}` : ""})`
            )
            .join(" · ")}
        />
      ) : null}

      <MetricGrid columns={4} className="mb-6">
        <MetricTile label="Catalogs harvested" value={report?.sources_harvested ?? 0} loading={reportQuery.isLoading} />
        <MetricTile label="Tables with location" value={report?.table_count ?? 0} loading={reportQuery.isLoading} />
        <MetricTile
          label="Overlapping groups"
          value={report?.overlapping_group_count ?? 0}
          loading={reportQuery.isLoading}
          emphasis={(report?.overlapping_group_count ?? 0) > 0}
        />
        <MetricTile
          label="Tables involved"
          value={report?.overlapping_table_count ?? 0}
          loading={reportQuery.isLoading}
        />
      </MetricGrid>

      <p className="mb-4 text-xs text-muted-foreground">
        {hasSnapshot
          ? `Last harvest ${formatTime(report?.generated_at)}. Slack alert fires at 10:00 IST only when groups exist.`
          : "No harvest yet. Refresh to list warehouse locations from every enabled catalog (Hive, Glue, REST)."}
      </p>

      <AsyncBoundary
        isLoading={reportQuery.isLoading}
        isError={false}
        isEmpty={isClear || !hasSnapshot}
        emptyTitle={!hasSnapshot ? "No shared-S3 harvest yet" : "No shared S3 paths"}
        emptyDescription={
          !hasSnapshot
            ? "Cryo reads Hive/Glue warehouse locations without loading Iceberg metadata, so unreadable aliases still show up."
            : "Every catalog table has a unique warehouse prefix. Safe to drop a name only after this stays empty."
        }
        emptyAction={
          !hasSnapshot ? (
            <Button variant="primary" onClick={() => refresh.mutate()} disabled={refresh.isPending}>
              Harvest catalogs
            </Button>
          ) : undefined
        }
      >
        {groups.length > 0 ? (
          <DataTable
            columns={columns}
            data={pageGroups}
            getRowId={(row) => row.shared_path}
            emptyTitle="No overlapping groups"
            pagination={{
              pageIndex,
              pageSize: PAGE_SIZE,
              totalRows: groups.length,
              onPageChange: setPageIndex
            }}
          />
        ) : null}
      </AsyncBoundary>

      {groups.length > 0 ? (
        <p className="mt-4 flex items-start gap-2 text-xs text-muted-foreground">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
          Do not DROP TABLE until the shared path is unique. Unregister / drop without purge to remove a duplicate name.
        </p>
      ) : null}
    </div>
  );
};
