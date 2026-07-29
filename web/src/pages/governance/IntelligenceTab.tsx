import type { ColumnDef } from "@tanstack/react-table";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  getCostForecast,
  getTableLineage,
  listMaintenanceSignals,
  listFleetAnomalies,
  listTables
} from "../../api/client";
import { AsyncBoundary } from "../../components/AsyncBoundary";
import { EmptyState, QueryErrorAlert, TelemetryRequiredNotice } from "../../components/common";
import { StatusTag } from "../../components/status";
import { Badge } from "../../components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/ui/card";
import { DataTable } from "../../components/ui/data-table";
import { useAuth } from "../../lib/auth";
import { formatCurrency } from "../../lib/format";
import { qualifiedTableName } from "../../lib/tableLabels";

const SIGNAL_PAGE_SIZE = 8;

const severityBadge = (value: string): "danger" | "warning" | "secondary" => {
  if (value === "critical") return "danger";
  if (value === "warning") return "warning";
  return "secondary";
};

export const IntelligenceTab = () => {
  const { api } = useAuth();
  const [lineageTableId, setLineageTableId] = useState("");
  const [signalPage, setSignalPage] = useState(0);

  const anomalies = useQuery({
    queryKey: ["fleet-anomalies", api.token],
    queryFn: () => listFleetAnomalies(api),
    refetchInterval: 60_000
  });
  const maintenanceSignals = useQuery({
    queryKey: ["maintenance-signals", api.token],
    queryFn: () => listMaintenanceSignals(api),
    refetchInterval: 60_000
  });
  const forecast = useQuery({
    queryKey: ["cost-forecast", api.token],
    queryFn: () => getCostForecast(api)
  });
  const tableOptions = useQuery({
    queryKey: ["governance-table-picker", api.token],
    queryFn: () => listTables(api, { limit: 200, offset: 0 })
  });
  const lineage = useQuery({
    queryKey: ["table-lineage", api.token, lineageTableId],
    queryFn: () => getTableLineage(api, lineageTableId),
    enabled: lineageTableId.length > 8
  });

  const telemetryEmpty =
    !anomalies.isLoading &&
    !maintenanceSignals.isLoading &&
    !anomalies.error &&
    !maintenanceSignals.error &&
    (anomalies.data ?? []).length === 0 &&
    (maintenanceSignals.data ?? []).length === 0;

  const signalsData = maintenanceSignals.data ?? [];
  const signalPageData = signalsData.slice(
    signalPage * SIGNAL_PAGE_SIZE,
    (signalPage + 1) * SIGNAL_PAGE_SIZE
  );

  const signalColumns = useMemo<ColumnDef<(typeof signalsData)[number], unknown>[]>(
    () => [
      {
        id: "table",
        header: "Table",
        cell: ({ row }) => (
          <Link to={`/tables/${row.original.table_id}`} className="text-primary hover:underline">
            {row.original.table_name}
          </Link>
        )
      },
      { accessorKey: "signal_type", header: "Type", size: 140 },
      {
        accessorKey: "severity",
        header: "Severity",
        size: 100,
        cell: ({ getValue }) => <Badge variant={severityBadge(String(getValue()))}>{String(getValue())}</Badge>
      },
      {
        accessorKey: "current_value",
        header: "Current",
        size: 80,
        cell: ({ getValue }) => (
          <span className="block text-right [font-variant-numeric:tabular-nums]">
            {(getValue() as number).toFixed(2)}
          </span>
        )
      },
      {
        accessorKey: "description",
        header: "Description",
        cell: ({ getValue }) => (
          <span className="block max-w-md truncate" title={String(getValue())}>
            {String(getValue())}
          </span>
        )
      }
    ],
    []
  );

  const lineageColumns = useMemo<ColumnDef<{ table_id: string; table_name: string; node_type: string }, unknown>[]>(
    () => [
      { accessorKey: "table_name", header: "Table" },
      { accessorKey: "node_type", header: "Edge", size: 100 }
    ],
    []
  );

  return (
    <>
      {telemetryEmpty ? <TelemetryRequiredNotice surface="Governance insights" /> : null}

      <div className="grid grid-cols-1 gap-3">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle>Maintenance signals</CardTitle>
          </CardHeader>
          <CardContent>
            <QueryErrorAlert error={maintenanceSignals.error} title="Failed to load maintenance signals" />
            <AsyncBoundary
              isLoading={maintenanceSignals.isLoading}
              isError={Boolean(maintenanceSignals.error)}
              error={maintenanceSignals.error}
            >
              <DataTable
                columns={signalColumns}
                data={signalPageData}
                density="compact"
                getRowId={(row) => `${row.table_id}-${row.signal_type}-${row.detected_at}`}
                emptyTitle="No maintenance signals"
                emptyDescription="File degradation, latency spikes, and cost spikes appear here when detected."
                pagination={
                  signalsData.length > SIGNAL_PAGE_SIZE
                    ? {
                        pageIndex: signalPage,
                        pageSize: SIGNAL_PAGE_SIZE,
                        totalRows: signalsData.length,
                        onPageChange: setSignalPage
                      }
                    : undefined
                }
              />
            </AsyncBoundary>
          </CardContent>
        </Card>

        <Card className="lg:max-w-md">
          <CardHeader className="pb-2">
            <CardTitle>FinOps forecast</CardTitle>
          </CardHeader>
          <CardContent>
            <QueryErrorAlert error={forecast.error} title="Failed to load cost forecast" />
            <AsyncBoundary
              isLoading={forecast.isLoading}
              isError={Boolean(forecast.error)}
              error={forecast.error}
              isEmpty={!forecast.data}
              empty={<EmptyState title="No forecast data" />}
            >
              {forecast.data ? (
                <div className="flex flex-col gap-2">
                  <p className="text-sm text-muted-foreground">Forecast month: {forecast.data.forecast_month}</p>
                  <p className="text-2xl font-semibold">{formatCurrency(forecast.data.total_forecast_usd)}</p>
                  <div className="flex flex-wrap gap-2">
                    <Badge variant="secondary">Storage {formatCurrency(forecast.data.forecast_storage_cost_usd)}</Badge>
                    <Badge variant="secondary">Compute {formatCurrency(forecast.data.forecast_compute_cost_usd)}</Badge>
                    <Badge variant="secondary">
                      Maintenance {formatCurrency(forecast.data.forecast_maintenance_cost_usd)}
                    </Badge>
                  </div>
                  <StatusTag status={forecast.data.alert_level} />
                </div>
              ) : null}
            </AsyncBoundary>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle>Table lineage</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <select
              className="h-[var(--control-height)] max-w-md rounded-[var(--radius-default)] border border-border bg-background px-3 text-sm"
              value={lineageTableId}
              disabled={tableOptions.isLoading}
              onChange={(event) => setLineageTableId(event.target.value)}
            >
              <option value="">Select a table for lineage</option>
              {(tableOptions.data?.items ?? []).map((table) => (
                <option key={table.id} value={table.id}>
                  {qualifiedTableName(table)}
                </option>
              ))}
            </select>
            <QueryErrorAlert error={lineage.error} title="Failed to load lineage" />
            {lineage.data ? (
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                <div>
                  <h4 className="mb-2 text-sm font-medium">Upstream</h4>
                  <DataTable
                    columns={lineageColumns}
                    data={lineage.data.upstream}
                    density="compact"
                    getRowId={(row) => row.table_id}
                    emptyTitle="No upstream tables"
                  />
                </div>
                <div>
                  <h4 className="mb-2 text-sm font-medium">Downstream</h4>
                  <DataTable
                    columns={lineageColumns}
                    data={lineage.data.downstream}
                    density="compact"
                    getRowId={(row) => row.table_id}
                    emptyTitle="No downstream tables"
                  />
                </div>
              </div>
            ) : (
              <EmptyState
                title="Enter a table ID"
                description="Lineage is built from query-log ingestion; complex SQL may produce incomplete graphs."
              />
            )}
          </CardContent>
        </Card>
      </div>
    </>
  );
};
