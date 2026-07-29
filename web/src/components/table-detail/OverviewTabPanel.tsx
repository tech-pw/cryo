import { OverviewTab } from "./OverviewTab";
import { RecommendedActions } from "./RecommendedActions";
import { TableParamOverrides } from "../TableParamOverrides";
import { AsyncBoundary } from "../AsyncBoundary";
import { useTableDetailContext } from "../../pages/table-detail/TableDetailContext";

export const OverviewTabPanel = () => {
  const ctx = useTableDetailContext();

  return (
    <AsyncBoundary
      isLoading={ctx.table.isLoading || ctx.healthTrends.isLoading}
      isError={Boolean(ctx.table.error)}
      error={ctx.table.error}
      onRetry={() => void ctx.table.refetch()}
    >
      <div className="td-tab-content">
        <OverviewTab
          score={ctx.score}
          scoreFactors={ctx.scoreFactors}
          rowExists={Boolean(ctx.row)}
          verdict={ctx.verdict}
          healthTrends={ctx.healthTrends.data}
          trendDirection={ctx.trendDirection}
          trendLabel={ctx.trendLabel}
          tableSignals={ctx.tableSignals.data}
          tableSignalsLoading={ctx.tableSignals.isLoading}
          tableSignalsError={ctx.tableSignals.error}
          costView={ctx.costView}
          benefitScores={ctx.benefitScores.data}
          stats={ctx.stats}
          statsRaw={ctx.statsRaw}
          reclaimableBytes={ctx.reclaimableBytes}
          savingsUsd={ctx.savingsUsd}
          presetOptions={ctx.assignPresetOptions}
          presetsLoading={ctx.presets.isLoading || ctx.compatiblePresets.isLoading}
          effectivePresetId={ctx.effectivePreset.data?.preset_id ?? undefined}
          onAssignPreset={(presetId) => ctx.assign.mutate(presetId)}
          assignPending={ctx.assign.isPending}
          onRun={() => ctx.run.mutate()}
          runPending={ctx.run.isPending}
          queuedMaintenanceRunId={ctx.queuedMaintenanceRunId}
          lastRunSkipNotes={ctx.lastRunSkipNotes}
          onOpenRun={ctx.handleRunAction}
          onPreview={() => ctx.setPreviewOpen(true)}
          onSchedule={(scheduledAtIso) => ctx.schedule.mutate(scheduledAtIso)}
          schedulePending={ctx.schedule.isPending}
          tableLabel={ctx.row ? `${ctx.row.schema_name}.${ctx.row.table_name}` : "this table"}
          partitionEvolution={ctx.partitionEvolution.data ?? undefined}
          tableQueries={ctx.tableQueries.data ?? undefined}
          tableQueriesLoading={ctx.tableQueries.isLoading}
          pointDeductions={ctx.pointDeductions}
        />
        <RecommendedActions
          costView={ctx.costView}
          signals={ctx.tableSignals.data}
          benefitScores={ctx.benefitScores.data}
          onPreview={() => ctx.setPreviewOpen(true)}
          onRun={() => ctx.run.mutate()}
        />
        <TableParamOverrides tableId={ctx.tableId} />
      </div>
    </AsyncBoundary>
  );
};
