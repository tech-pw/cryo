import type { ColumnDef } from "@tanstack/react-table";
import { Plus } from "lucide-react";
import { useMemo } from "react";
import { patchSource } from "../../api/client";
import type { SourceRead } from "../../api/types";
import { AwsStatusBadge } from "../../components/AwsStatusBadge";
import { ConfirmAction } from "../../components/confirm";
import { StatusTag } from "../../components/status";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { DataTable } from "../../components/ui/data-table";
import { isConnectionPendingScan } from "../../lib/connectionScanPrompt";
import { useConnectionsContext } from "./ConnectionsContext";

export const LakehousesTab = () => {
  const {
    api,
    sources,
    awsStatuses,
    pendingScanVersion,
    setSourceDrawer,
    setLatencySource,
    purgeSourceMutation,
    queryClient
  } = useConnectionsContext();

  const columns = useMemo<ColumnDef<SourceRead, unknown>[]>(
    () => [
      {
        accessorKey: "name",
        header: "Name",
        cell: ({ row }) => (
          <div className="flex items-center gap-2">
            <span className="font-medium">{row.original.name}</span>
            {isConnectionPendingScan("lakehouse", row.original.id) ? (
              <Badge variant="warning">Scan required</Badge>
            ) : null}
          </div>
        )
      },
      { accessorKey: "type", header: "Type" },
      {
        id: "aws",
        header: "AWS",
        cell: ({ row }) => <AwsStatusBadge status={awsStatuses[row.original.id]} />
      },
      {
        accessorKey: "enabled",
        header: "Enabled",
        cell: ({ getValue }) => <StatusTag status={getValue() ? "succeeded" : "skipped"} />
      },
      {
        accessorKey: "config",
        header: "Config",
        cell: ({ getValue }) => {
          const value = getValue() as Record<string, unknown> | undefined;
          return <code className="text-xs">{Object.keys(value ?? {}).join(", ") || "-"}</code>;
        }
      },
      {
        id: "actions",
        header: "",
        size: 320,
        cell: ({ row }) => (
          <div className="flex flex-wrap items-center gap-1">
            <Button size="sm" variant="secondary" onClick={() => setSourceDrawer(row.original)}>
              Edit
            </Button>
            <Button size="sm" variant="secondary" onClick={() => setLatencySource(row.original)}>
              Latency
            </Button>
            <Button
              size="sm"
              variant="secondary"
              onClick={() =>
                patchSource(api, row.original.id, { enabled: !row.original.enabled }).then(() =>
                  queryClient.invalidateQueries({ queryKey: ["sources"] })
                )
              }
            >
              {row.original.enabled ? "Disable" : "Enable"}
            </Button>
            <ConfirmAction
              title={`Permanently delete "${row.original.name}"?`}
              description="This deletes the lakehouse and everything Cryo scanned from it -- all its tables, health/cost history, and tier policies. This cannot be undone. If you just want to pause it, use Disable instead."
              onConfirm={() => purgeSourceMutation.mutate(row.original.id)}
            >
              <Button size="sm" variant="danger" loading={purgeSourceMutation.isPending}>
                Delete
              </Button>
            </ConfirmAction>
          </div>
        )
      }
    ],
    [api, awsStatuses, purgeSourceMutation, queryClient, setLatencySource, setSourceDrawer]
  );

  return (
    <div className="flex w-full flex-col gap-3">
      <p className="tab-intro text-sm text-muted-foreground">
        The Iceberg catalogs your data lakehouse lives in — REST catalog, Hive Metastore, or Glue. Cryo reads
        these to discover tables and scan health. Read-only: nothing here ever runs maintenance, it only tells
        Cryo what tables exist.
      </p>
      <div>
        <Button variant="primary" onClick={() => setSourceDrawer("new")}>
          <Plus className="size-4" aria-hidden="true" />
          New lakehouse
        </Button>
      </div>
      <DataTable
        key={`lakehouses-${pendingScanVersion}`}
        columns={columns}
        data={sources.data ?? []}
        density="compact"
        loading={sources.isLoading}
        getRowId={(row) => row.id ?? `source-${row.type}-${row.name}`}
        emptyTitle="No lakehouses connected"
        emptyDescription="Connect a catalog to start scanning Iceberg tables."
      />
    </div>
  );
};
