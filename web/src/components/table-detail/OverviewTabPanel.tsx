import { Link } from "react-router-dom";
import { OverviewTab } from "./OverviewTab";
import { ComplianceOwnershipSummary } from "./ComplianceOwnershipSummary";
import { TableParamOverrides } from "../TableParamOverrides";
import { AsyncBoundary } from "../AsyncBoundary";
import { Alert } from "../ui/alert";
import { useTableDetailContext } from "../../pages/table-detail/TableDetailContext";
import { formatTime } from "../../lib/format";
import type { FastPathFindingRead } from "../../api/client";

const MISSING_TARGET_FILE_SIZE = "missing_target_file_size";

const findingAlertVariant = (severity: string): "error" | "warning" | "info" => {
  const normalized = severity.toLowerCase();
  if (normalized === "critical" || normalized === "error") return "error";
  if (normalized === "warning") return "warning";
  return "info";
};

const FastPathTableBanner = ({
  tableId,
  findings
}: {
  tableId: string;
  findings: FastPathFindingRead[];
}) => {
  if (!findings.length) return null;
  const propertiesHref = `/tables/${tableId}?tab=config#config-properties`;

  return (
    <div className="mb-3 flex flex-col gap-2">
      {findings.map((finding, index) => (
        <Alert
          key={`${finding.finding_type}-${finding.detected_at}-${index}`}
          variant={findingAlertVariant(finding.severity)}
          title="Recently discovered — early health flag"
          description={
            <span>
              {finding.reason}
              {finding.detected_at ? ` Flagged ${formatTime(finding.detected_at)}.` : ""}
              {finding.finding_type === MISSING_TARGET_FILE_SIZE ? (
                <>
                  {" "}
                  This table has no <code>write.target-file-size-bytes</code> set — see{" "}
                  <Link to={propertiesHref} className="font-medium text-primary hover:underline">
                    property recommendations
                  </Link>{" "}
                  for the one-click fix. Compaction is not started automatically.
                </>
              ) : null}
            </span>
          }
        />
      ))}
    </div>
  );
};

export const OverviewTabPanel = () => {
  const ctx = useTableDetailContext();
  const tableFindings = (ctx.fastPathFindings.data ?? []).filter((item) => item.table_id === ctx.tableId);

  return (
    <AsyncBoundary
      isLoading={ctx.table.isLoading || ctx.healthTrends.isLoading}
      isError={Boolean(ctx.table.error)}
      error={ctx.table.error}
      onRetry={() => void ctx.table.refetch()}
    >
      <div className="td-tab-content">
        <FastPathTableBanner tableId={ctx.tableId} findings={tableFindings} />
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
          effectivePresetId={ctx.effectivePreset.data?.preset_id ?? undefined}
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
        <div className="td-overview-supporting">
          <TableParamOverrides tableId={ctx.tableId} />
          <ComplianceOwnershipSummary />
        </div>
      </div>
    </AsyncBoundary>
  );
};
