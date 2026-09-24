import { AsyncBoundary } from "../AsyncBoundary";
import { useTableDetailContext } from "../../pages/table-detail/TableDetailContext";
import { TableAiInsights } from "../TableAiInsights";
import { TableChecklistPanel } from "../ChecklistMatrix";
import { TableCompactionAnalytics } from "../TableCompactionAnalytics";
import { ChartCard } from "./ChartCard";
import { PropertyRecommendations } from "./PropertyRecommendations";
import { QualityTab } from "./QualityTab";
import { SectionNav } from "./SectionNav";
import { WairRecluster } from "./WairRecluster";

/**
 * Previously: outer page Tabs -> a 2-column grid of ChartCards -> one ChartCard containing
 * its own inner 5-tab strip (PII/Similar/Risk/Anomalies/AutoComp) -- three nesting levels for
 * one tab. Flattened to the same SectionNav anchor-rail pattern the Structure tab already
 * uses: one level of zones, each a full-width section, no nested Tabs-in-Tabs.
 */
export const HealthQualityTabPanel = () => {
  const { row, tableId, lastSnapshotCommittedAt, table } = useTableDetailContext();

  return (
    <div className="td-tab-content">
      <SectionNav
        sections={[
          { id: "hq-checklist", label: "Optimization Checklist" },
          { id: "hq-quality", label: "Quality Overview" },
          { id: "hq-properties", label: "Properties" },
          { id: "hq-compaction", label: "Compaction" },
          { id: "hq-ai-signals", label: "AI Signals" }
        ]}
      />
      <div id="hq-checklist">
        <AsyncBoundary isLoading={table.isLoading}>{row ? <TableChecklistPanel tableId={tableId} /> : null}</AsyncBoundary>
      </div>
      <div id="hq-quality" style={{ marginTop: 16 }}>
        <QualityTab />
      </div>
      <div id="hq-properties" style={{ marginTop: 16 }}>
        <PropertyRecommendations />
        <WairRecluster />
      </div>
      <div id="hq-compaction" style={{ marginTop: 16 }}>
        <ChartCard title="Compaction">
          <AsyncBoundary isLoading={table.isLoading}>
            {row ? <TableCompactionAnalytics tableId={tableId} embedded compact /> : null}
          </AsyncBoundary>
        </ChartCard>
      </div>
      <div id="hq-ai-signals" style={{ marginTop: 16 }}>
        <ChartCard title="AI signals">
          <AsyncBoundary isLoading={table.isLoading}>
            {row ? (
              <TableAiInsights
                tableId={tableId}
                lastSnapshotCommittedAt={lastSnapshotCommittedAt}
                embedded
                visibleTabs={["pii", "similar", "risk", "anomalies", "autocomp"]}
              />
            ) : null}
          </AsyncBoundary>
        </ChartCard>
      </div>
    </div>
  );
};
