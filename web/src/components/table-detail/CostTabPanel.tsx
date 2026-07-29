import { CostTab } from "./CostTab";
import { CompactionCandidatesTab } from "./CompactionCandidatesTab";
import { AsyncBoundary } from "../AsyncBoundary";
import { useTableDetailContext } from "../../pages/table-detail/TableDetailContext";

export const CostTabPanel = () => {
  const {
    api,
    tableId,
    costView,
    stats,
    storageBytes,
    hasStatsCostFallback,
    currentCostUsd,
    projectedCostUsd,
    savingsUsd,
    costByOperation,
    s3Tiering,
    tableCost
  } = useTableDetailContext();

  return (
    <div className="td-tab-content">
      <AsyncBoundary
        isLoading={tableCost.isLoading}
        isError={Boolean(tableCost.error)}
        error={tableCost.error}
        onRetry={() => void tableCost.refetch()}
      >
        <CostTab
          costView={costView}
          stats={stats}
          storageBytes={storageBytes}
          hasStatsCostFallback={hasStatsCostFallback}
          currentCostUsd={currentCostUsd}
          projectedCostUsd={projectedCostUsd}
          savingsUsd={savingsUsd}
          costByOperation={costByOperation}
          s3Tiering={s3Tiering.data ?? null}
        />
      </AsyncBoundary>
      <div style={{ marginTop: 16 }}>
        <CompactionCandidatesTab api={api} tableId={tableId} />
      </div>
    </div>
  );
};
