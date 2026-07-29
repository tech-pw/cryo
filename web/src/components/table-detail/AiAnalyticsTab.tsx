import { Bot } from "lucide-react";
import { TableAiInsights } from "../TableAiInsights";
import { TableCompactionAnalytics } from "../TableCompactionAnalytics";
import { AsyncBoundary } from "../AsyncBoundary";
import { useTableDetailContext } from "../../pages/table-detail/TableDetailContext";
import { ChartCard } from "./ChartCard";

export const AiAnalyticsTabLabel = () => (
  <span>
    <Bot className="mr-1 inline size-3.5" />
    AI &amp; Analytics
  </span>
);

export const AiAnalyticsTab = () => {
  const { row, tableId, lastSnapshotCommittedAt, table } = useTableDetailContext();

  return (
    <AsyncBoundary isLoading={table.isLoading}>
      <div className="td-tab-content">
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          <ChartCard title="Compaction analytics">
            {row ? <TableCompactionAnalytics tableId={tableId} embedded /> : null}
          </ChartCard>
          <ChartCard title="AI observability">
            {row ? (
              <TableAiInsights
                tableId={tableId}
                lastSnapshotCommittedAt={lastSnapshotCommittedAt}
                embedded
                visibleTabs={["pii", "similar", "risk", "anomalies", "autocomp"]}
              />
            ) : null}
          </ChartCard>
        </div>
      </div>
    </AsyncBoundary>
  );
};
