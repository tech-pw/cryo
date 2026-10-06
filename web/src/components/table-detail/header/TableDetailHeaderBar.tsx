import { useQuery } from "@tanstack/react-query";
import { getTableAudit } from "../../../api/client";
import { formatEngineLabel } from "../../../lib/engines";
import { useWriteControl } from "../../../lib/useWriteControl";
import { qualifiedTableName } from "../../../lib/tableLabels";
import { useTableDetailContext } from "../../../pages/table-detail/TableDetailContext";
import { tableKeys } from "../../../pages/table-detail/tableKeys";
import { useOperationLauncher } from "../operations/OperationLauncher";
import { OPERATION_CATALOG, RUNNABLE_OPERATIONS, type RunnableOperation } from "../operations/operationCatalog";
import { TableDetailHeader } from "./TableDetailHeader";

const HEADER_OPERATIONS = RUNNABLE_OPERATIONS.map((op) => ({
  op,
  label: OPERATION_CATALOG[op].title,
  destructive: OPERATION_CATALOG[op].destructive
}));

const knownNumber = (value: number | null | undefined): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

export const TableDetailHeaderBar = () => {
  const ctx = useTableDetailContext();
  const runAccess = useWriteControl("runMaintenance");
  const workflowAccess = useWriteControl("assignPreset");
  const scanAccess = useWriteControl("triggerScan");
  const launcher = useOperationLauncher();
  const row = ctx.row;
  const stats = ctx.stats;
  const receipt = useQuery({
    queryKey: tableKeys.audit(ctx.tableId, ctx.token, 1),
    queryFn: () => getTableAudit(ctx.api, ctx.tableId, { limit: 1 }),
    enabled: Boolean(ctx.tableId)
  });
  const latest = receipt.data?.[0];
  const failures = row?.consecutive_scan_failures ?? 0;
  const presetId = ctx.effectivePreset.data?.preset_id ?? null;
  const engine = ctx.effectiveDestination?.engine ?? null;
  const writeReason =
    runAccess.reason ?? (presetId ? null : "Assign a workflow before running maintenance");

  return (
    <TableDetailHeader
      tableId={ctx.tableId}
      tableName={row?.table_name ?? "Table"}
      qualifiedName={row ? qualifiedTableName(row) : null}
      sourceName={row?.source_name || ctx.sourceName || "Catalog"}
      catalogName={row?.catalog_name}
      schemaName={row?.schema_name ?? "—"}
      scanned={Boolean(stats)}
      score={knownNumber(stats?.overall_score)}
      grade={stats?.grade ?? null}
      ownerEmail={row?.owner_email ?? null}
      partitionLabel={ctx.partitionColLabel}
      location={row?.location ?? null}
      scanAt={stats?.scan_at ?? null}
      lastCommitAt={ctx.lastSnapshotCommittedAt}
      sizeGb={knownNumber(stats?.total_size_gb)}
      totalFiles={knownNumber(stats?.total_files)}
      smallFiles={knownNumber(stats?.small_files_count)}
      snapshotCount={knownNumber(stats?.total_snapshots)}
      lastMaintainedAt={latest?.ts ?? latest?.created_at ?? null}
      lastMaintainedOperation={latest?.action ?? null}
      workflowName={ctx.effectivePreset.data?.preset_name ?? null}
      workflowSource={ctx.effectivePreset.data?.provenance ?? null}
      hasWorkflow={Boolean(presetId)}
      engineLabel={engine ? formatEngineLabel(engine, ctx.effectiveDestination?.config) : null}
      engineSupported={Boolean(engine)}
      engineReason={engine ? null : "No workflow destination is assigned"}
      scanBackoff={
        failures >= 3
          ? { until: null, failures }
          : null
      }
      writeAllowed={runAccess.allowed && Boolean(presetId)}
      writeReason={writeReason}
      chooseWorkflowAllowed={workflowAccess.allowed}
      chooseWorkflowReason={workflowAccess.reason}
      scanAllowed={scanAccess.allowed}
      scanReason={scanAccess.reason}
      onScan={() => ctx.scan.mutate()}
      onRefreshStats={() => ctx.scan.mutate()}
      onChooseWorkflow={() => {
        const next = new URLSearchParams(ctx.searchParams);
        next.set("tab", "maintenance");
        ctx.navigate({ search: `?${next.toString()}`, hash: "#maintenance-settings" });
      }}
      onPreview={() => ctx.setPreviewOpen(true)}
      onRun={() => ctx.run.mutate()}
      operations={launcher ? HEADER_OPERATIONS : []}
      onRunOperation={(op) => launcher?.launch(op as RunnableOperation)}
      onOpenRunOnDemand={() => {
        ctx.handleTabChange("maintenance");
        window.setTimeout(() => document.getElementById("run-on-demand")?.scrollIntoView({ behavior: "smooth", block: "start" }), 250);
      }}
      operationsDisabledReason={launcher?.blockedReason ?? null}
      onOpenSettings={() => ctx.setSettingsOpen(true)}
      runPending={ctx.run.isPending}
      scanPending={ctx.scan.isPending}
    />
  );
};
