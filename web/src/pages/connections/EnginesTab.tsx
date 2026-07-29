import type { ColumnDef } from "@tanstack/react-table";
import { Plus } from "lucide-react";
import { useMemo } from "react";
import { patchDestination } from "../../api/client";
import type { DestinationRead } from "../../api/types";
import { ConfirmAction } from "../../components/confirm";
import { HealthBadge } from "../../components/status";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { DataTable } from "../../components/ui/data-table";
import { engineTagColor, formatEngineLabel } from "../../lib/engines";
import { isConnectionPendingScan } from "../../lib/connectionScanPrompt";
import { useConnectionsContext } from "./ConnectionsContext";

const engineBadgeVariant = (engine: string): "default" | "warning" | "secondary" => {
  const color = engineTagColor(engine);
  if (color === "orange") return "warning";
  if (color === "blue" || color === "purple") return "default";
  return "secondary";
};

export const EnginesTab = () => {
  const {
    api,
    destinations,
    pendingScanVersion,
    setDestinationDrawer,
    probe,
    purgeDestinationMutation,
    queryClient
  } = useConnectionsContext();

  const columns = useMemo<ColumnDef<DestinationRead, unknown>[]>(
    () => [
      {
        accessorKey: "name",
        header: "Name",
        cell: ({ row }) => (
          <div className="flex items-center gap-2">
            <span className="font-medium">{row.original.name}</span>
            {isConnectionPendingScan("engine", row.original.id) ? (
              <Badge variant="warning">Scan lakehouse first</Badge>
            ) : null}
          </div>
        )
      },
      {
        accessorKey: "engine",
        header: "Engine",
        cell: ({ row }) => (
          <Badge variant={engineBadgeVariant(row.original.engine)}>
            {formatEngineLabel(row.original.engine, row.original.config)}
          </Badge>
        )
      },
      {
        accessorKey: "enabled",
        header: "Enabled",
        cell: ({ getValue }) => (
          <HealthBadge healthy={Boolean(getValue())}>{getValue() ? "Enabled" : "Disabled"}</HealthBadge>
        )
      },
      {
        id: "offpeak",
        header: "Off-peak window",
        cell: ({ row }) =>
          row.original.offpeak_start === null || row.original.offpeak_start === undefined
            ? "-"
            : `${row.original.offpeak_start}:00-${row.original.offpeak_end}:00`
      },
      {
        id: "actions",
        header: "",
        size: 300,
        cell: ({ row }) => (
          <div className="flex flex-wrap items-center gap-1">
            <Button size="sm" variant="secondary" onClick={() => setDestinationDrawer(row.original)}>
              Edit
            </Button>
            <Button size="sm" variant="secondary" loading={probe.isPending} onClick={() => probe.mutate(row.original)}>
              Test
            </Button>
            <Button
              size="sm"
              variant="secondary"
              onClick={() =>
                patchDestination(api, row.original.id, { enabled: !row.original.enabled }).then(() =>
                  queryClient.invalidateQueries({ queryKey: ["destinations"] })
                )
              }
            >
              {row.original.enabled ? "Disable" : "Enable"}
            </Button>
            <ConfirmAction
              title={`Permanently delete "${row.original.name}"?`}
              description="This cannot be undone. If any presets or maintenance runs still reference this engine, deletion will fail until those are reassigned or removed. If you just want to pause it, use Disable instead."
              onConfirm={() => purgeDestinationMutation.mutate(row.original.id)}
            >
              <Button size="sm" variant="danger" loading={purgeDestinationMutation.isPending}>
                Delete
              </Button>
            </ConfirmAction>
          </div>
        )
      }
    ],
    [api, probe, purgeDestinationMutation, queryClient, setDestinationDrawer]
  );

  return (
    <div className="flex w-full flex-col gap-3">
      <p className="tab-intro text-sm text-muted-foreground">
        The processing engines that actually run maintenance — Trino, Spark/EMR, or native iceberg-go /
        iceberg-rust subprocess engines. Presets and manual runs target one of these to do the real work
        (compaction, snapshot expiry, orphan cleanup).
      </p>
      <div>
        <Button variant="primary" onClick={() => setDestinationDrawer("new")}>
          <Plus className="size-4" aria-hidden="true" />
          New engine
        </Button>
      </div>
      <DataTable
        key={`engines-${pendingScanVersion}`}
        columns={columns}
        data={destinations.data ?? []}
        density="compact"
        loading={destinations.isLoading}
        getRowId={(row) => row.id ?? `destination-${row.engine}-${row.name}`}
        emptyTitle="No engines connected"
        emptyDescription="Add a Trino, Spark, or native engine to run maintenance."
      />
    </div>
  );
};
