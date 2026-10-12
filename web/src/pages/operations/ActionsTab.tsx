import type { ColumnDef } from "@tanstack/react-table";
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { listFleetAudit, type FleetAuditItem } from "../../api/fleetAudit";
import { StatusTag } from "../../components/status";
import { DataTable } from "../../components/ui/data-table";
import { Select } from "../../components/ui/select";
import { useAuth } from "../../lib/auth";
import { auditBytesLabel, auditFilesLabel } from "../../lib/auditFeed";
import { formatDuration, formatTime } from "../../lib/format";
import { CORRECT_ORDER } from "../../lib/operationOrder";

const setParam = (prev: URLSearchParams, key: string, value: string) => {
  const next = new URLSearchParams(prev);
  next.set("tab", "actions");
  if (value) next.set(key, value);
  else next.delete(key);
  return next;
};

const operationLabel = (value: string) => value.replaceAll("_", " ");

const OPERATION_OPTIONS = CORRECT_ORDER.map((value) => ({
  value,
  label: operationLabel(value)
}));

const STATUS_OPTIONS = [
  { value: "OK", label: "Succeeded" },
  { value: "FAILED", label: "Failed" },
  { value: "SKIPPED", label: "Skipped" },
  { value: "SUBMITTED", label: "Submitted" }
];

const ENGINE_OPTIONS = [
  { value: "trino", label: "Trino" },
  { value: "spark_emr", label: "Spark EMR" },
  { value: "native", label: "Native" },
  { value: "flink", label: "Flink" }
];

export const ActionsTab = () => {
  const { api, token } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const operation = searchParams.get("operation") ?? "";
  const status = searchParams.get("status") ?? "";
  const engine = searchParams.get("engine") ?? "";

  const audit = useQuery({
    queryKey: ["fleet-audit", token, operation, status, engine],
    queryFn: () =>
      listFleetAudit(api, {
        limit: 200,
        action: operation || undefined,
        status: status || undefined,
        engine: engine || undefined
      })
  });

  const columns = useMemo<ColumnDef<FleetAuditItem, unknown>[]>(
    () => [
      {
        id: "ts",
        header: "Time",
        cell: ({ row }) => formatTime(row.original.ts)
      },
      {
        id: "action",
        header: "Operation",
        cell: ({ row }) => row.original.action
      },
      {
        id: "table",
        header: "Table",
        cell: ({ row }) =>
          row.original.table_id ? (
            <Link to={`/tables/${row.original.table_id}`} className="text-primary hover:underline">
              {row.original.schema_name && row.original.table_name
                ? `${row.original.schema_name}.${row.original.table_name}`
                : row.original.table_id.slice(0, 8)}
            </Link>
          ) : (
            "Unknown"
          )
      },
      {
        id: "engine",
        header: "Engine",
        cell: ({ row }) => row.original.engine || "Unknown"
      },
      {
        id: "duration",
        header: "Duration",
        cell: ({ row }) => formatDuration(row.original.duration_ms)
      },
      {
        id: "files",
        header: "Files",
        cell: ({ row }) => auditFilesLabel(row.original.input_files_count, row.original.output_files_count)
      },
      {
        id: "bytes",
        header: "Bytes",
        cell: ({ row }) => auditBytesLabel(row.original.actual_bytes_freed)
      },
      {
        id: "status",
        header: "Status",
        cell: ({ row }) => <StatusTag status={row.original.status} />
      }
    ],
    []
  );

  return (
    <div className="space-y-2">
      <div className="ops-actions-filters" role="group" aria-label="Filter actions">
        <Select
          aria-label="Operation"
          className="ops-actions-filters__control"
          placeholder="All operations"
          allowClear
          value={operation}
          options={OPERATION_OPTIONS}
          onChange={(value) => setSearchParams((prev) => setParam(prev, "operation", value ?? ""))}
        />
        <Select
          aria-label="Status"
          className="ops-actions-filters__control"
          placeholder="All statuses"
          allowClear
          value={status}
          options={STATUS_OPTIONS}
          onChange={(value) => setSearchParams((prev) => setParam(prev, "status", value ?? ""))}
        />
        <Select
          aria-label="Engine"
          className="ops-actions-filters__control"
          placeholder="All engines"
          allowClear
          value={engine}
          options={ENGINE_OPTIONS}
          onChange={(value) => setSearchParams((prev) => setParam(prev, "engine", value ?? ""))}
        />
      </div>
      <DataTable
        columns={columns}
        data={audit.data?.items ?? []}
        density="compact"
        loading={audit.isLoading}
        getRowId={(row) => String(row.id)}
        emptyTitle="No actions"
        emptyDescription="Maintenance audit rows appear here once a run records them."
      />
    </div>
  );
};
