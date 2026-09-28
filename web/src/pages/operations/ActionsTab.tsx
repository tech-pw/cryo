import type { ColumnDef } from "@tanstack/react-table";
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { listFleetAudit, type FleetAuditItem } from "../../api/fleetAudit";
import { StatusTag } from "../../components/status";
import { DataTable } from "../../components/ui/data-table";
import { Input } from "../../components/ui/input";
import { useAuth } from "../../lib/auth";
import { auditBytesLabel, auditFilesLabel } from "../../lib/auditFeed";
import { formatDuration, formatTime } from "../../lib/format";

const setParam = (prev: URLSearchParams, key: string, value: string) => {
  const next = new URLSearchParams(prev);
  next.set("tab", "actions");
  if (value) next.set(key, value);
  else next.delete(key);
  return next;
};

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
      <div className="flex flex-wrap gap-2">
        <Input
          aria-label="Operation"
          placeholder="Operation"
          value={operation}
          onChange={(event) =>
            setSearchParams((prev) => setParam(prev, "operation", event.target.value.trim()))
          }
        />
        <Input
          aria-label="Status"
          placeholder="Status"
          value={status}
          onChange={(event) => setSearchParams((prev) => setParam(prev, "status", event.target.value.trim()))}
        />
        <Input
          aria-label="Engine"
          placeholder="Engine"
          value={engine}
          onChange={(event) => setSearchParams((prev) => setParam(prev, "engine", event.target.value.trim()))}
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
