import type { ColumnDef } from "@tanstack/react-table";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { listPresetRules, listPresets, listRuns, simulatePreset } from "../../api/client";
import type { RunRead } from "../../api/types";
import { OperationPipeline, maintenanceOpsToSteps } from "../../components/operations/OperationPipeline";
import { MiniRunHistoryBar } from "../../components/operations/RunHistoryGrid";
import { StatusTag } from "../../components/status";
import { Alert } from "../../components/ui/alert";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/ui/card";
import { DataTable } from "../../components/ui/data-table";
import { useAuth } from "../../lib/auth";
import { formatGb, formatNumber, formatTime } from "../../lib/format";
import { formatPresetSummary } from "../../lib/presetForm";
import { shortRunId, targetLabel } from "../../lib/operations";
import { WorkflowDetailShell } from "./WorkflowDetailShell";
import { PresetConfigTab } from "./preset/PresetConfigTab";

const PresetRunsTab = ({ presetId }: { presetId: string }) => {
  const { api, token } = useAuth();
  const runs = useQuery({
    queryKey: ["preset-runs", presetId, token],
    queryFn: () => listRuns(api, { limit: 100 }).then((items) => items.filter((r) => r.preset_id === presetId))
  });

  const columns = useMemo<ColumnDef<RunRead, unknown>[]>(
    () => [
      {
        id: "run",
        header: "Run",
        cell: ({ row }) => (
          <Link
            to={`/operations/runs/${row.original.id}`}
            className="font-mono text-sm text-primary hover:underline"
            onClick={(e) => e.stopPropagation()}
          >
            {shortRunId(row.original.id)}
          </Link>
        )
      },
      {
        id: "status",
        header: "Status",
        cell: ({ row }) => <StatusTag status={row.original.status} />
      },
      { id: "target", header: "Target", cell: ({ row }) => targetLabel(row.original) },
      {
        id: "trigger",
        header: "Trigger",
        cell: ({ row }) => row.original.trigger ?? "manual"
      },
      {
        id: "created",
        header: "Created",
        cell: ({ row }) => formatTime(row.original.created_at)
      }
    ],
    []
  );

  return (
    <DataTable
      columns={columns}
      data={runs.data ?? []}
      density="compact"
      loading={runs.isLoading}
      getRowId={(row) => row.id}
      emptyTitle="No runs"
      emptyDescription="Runs for this preset will appear here."
    />
  );
};

const PresetOverviewTab = ({ presetId, presetName }: { presetId: string; presetName: string }) => {
  const { api, token } = useAuth();
  const presets = useQuery({ queryKey: ["presets", token], queryFn: () => listPresets(api) });
  const preset = presets.data?.find((item) => item.id === presetId);
  const runs = useQuery({
    queryKey: ["preset-runs-overview", presetId, token],
    queryFn: () => listRuns(api, { limit: 50 }).then((items) => items.filter((r) => r.preset_id === presetId))
  });
  const simulation = useQuery({
    queryKey: ["preset-simulation", presetId, token],
    queryFn: () => simulatePreset(api, presetId)
  });
  const rules = useQuery({
    queryKey: ["preset-rules", presetId, token],
    queryFn: () => listPresetRules(api, presetId)
  });

  const latest = runs.data?.[0];
  const latestFailed = runs.data?.find((run) => run.status === "failed");

  return (
    <div className="flex flex-col gap-3">
      <Card>
        <CardHeader className="pb-2">
          <CardTitle>Workflow status</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <p className="text-sm">
            <strong>{presetName}</strong> · {formatPresetSummary(preset ?? { operations: [], mode: "standard", params: {} } as never)}
          </p>
          <div className="flex flex-wrap gap-3 text-sm text-muted-foreground">
            <span>Schedule: {preset?.schedule_cron ?? "—"}</span>
            <span>Rules: {rules.data?.length ?? 0}</span>
            <span>Matched tables: {formatNumber(simulation.data?.matched_count)}</span>
            <span>Total size: {formatGb(simulation.data?.total_size_gb)}</span>
          </div>
          {latest ? (
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span>Last run:</span>
              <Link to={`/operations/runs/${latest.id}`} className="font-mono text-sm text-primary hover:underline">
                {shortRunId(latest.id)}
              </Link>
              <StatusTag status={latest.status} />
              <MiniRunHistoryBar runs={runs.data ?? []} />
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No runs yet.</p>
          )}
        </CardContent>
      </Card>
      {latestFailed ? (
        <Alert
          variant="error"
          title="Latest failure"
          description={
            <span className="text-sm">
              Run{" "}
              <Link to={`/operations/runs/${latestFailed.id}`} className="text-primary hover:underline">
                {shortRunId(latestFailed.id)}
              </Link>{" "}
              failed at {formatTime(latestFailed.finished_at ?? latestFailed.created_at)}
            </span>
          }
        />
      ) : null}
    </div>
  );
};

const PresetGridTab = ({ presetId }: { presetId: string }) => {
  const { api, token } = useAuth();
  const presets = useQuery({ queryKey: ["presets", token], queryFn: () => listPresets(api) });
  const preset = presets.data?.find((item) => item.id === presetId);
  const runs = useQuery({
    queryKey: ["preset-grid-runs", presetId, token],
    queryFn: () => listRuns(api, { limit: 10 }).then((items) => items.filter((r) => r.preset_id === presetId))
  });
  const ops = preset?.operations ?? [];

  const columns = useMemo<ColumnDef<{ key: string; operation: string }, unknown>[]>(() => {
    const base: ColumnDef<{ key: string; operation: string }, unknown>[] = [
      { id: "operation", header: "Operation", cell: ({ row }) => row.original.operation }
    ];
    const runCols = (runs.data ?? []).map(
      (run): ColumnDef<{ key: string; operation: string }, unknown> => ({
        id: run.id,
        header: shortRunId(run.id),
        size: 90,
        cell: () => (
          <Link to={`/operations/runs/${run.id}`}>
            <StatusTag status={run.status} />
          </Link>
        )
      })
    );
    return [...base, ...runCols];
  }, [runs.data]);

  const data = ops.map((op) => ({ key: op, operation: op }));

  return <DataTable columns={columns} data={data} density="compact" getRowId={(row) => row.key} />;
};

const PresetGraphTab = ({ presetId }: { presetId: string }) => {
  const { api, token } = useAuth();
  const presets = useQuery({ queryKey: ["presets", token], queryFn: () => listPresets(api) });
  const preset = presets.data?.find((item) => item.id === presetId);
  const steps = useMemo(
    () => maintenanceOpsToSteps(preset?.operations ?? []),
    [preset?.operations]
  );
  return <OperationPipeline steps={steps} title="Configured operations" />;
};

export const PresetDetail = () => {
  const { presetId } = useParams();
  const [searchParams] = useSearchParams();
  const { api, token } = useAuth();
  const queryClient = useQueryClient();

  const presetsQuery = useQuery({
    queryKey: ["presets", token],
    queryFn: () => listPresets(api),
    enabled: Boolean(presetId)
  });
  const preset = presetsQuery.data?.find((item) => item.id === presetId);

  const defaultTab = searchParams.get("tab") === "config" ? "config" : "overview";

  if (!presetId) return <Alert variant="error" title="Missing preset id" />;

  return (
    <WorkflowDetailShell
      title={preset?.name ?? "Preset"}
      subtitle={preset?.description ?? "Maintenance workflow"}
      defaultTab={defaultTab as "overview"}
      overview={<PresetOverviewTab presetId={presetId} presetName={preset?.name ?? "Preset"} />}
      grid={<PresetGridTab presetId={presetId} />}
      graph={<PresetGraphTab presetId={presetId} />}
      runs={<PresetRunsTab presetId={presetId} />}
      config={
        <PresetConfigTab
          preset={preset}
          loading={presetsQuery.isLoading}
          onSaved={() => {
            void queryClient.invalidateQueries({ queryKey: ["presets"] });
          }}
        />
      }
    />
  );
};
