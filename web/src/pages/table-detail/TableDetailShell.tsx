import { lazy, Suspense } from "react";
import { Link } from "react-router-dom";
import { PlanPreview } from "../../components/PlanPreview";
import { ConfirmAction } from "../../components/confirm";
import { AssetHeader } from "../../components/table-detail/AssetHeader";
import { EngineActionStrip } from "../../components/table-detail/EngineActionStrip";
import { TableSettingsSheet } from "../../components/table-detail/TableSettingsSheet";
import { AsyncBoundary } from "../../components/AsyncBoundary";
import {
  ActivityTabLabel,
  CostTabLabel,
  HealthQualityTabLabel,
  LineageTabLabel,
  OverviewTabLabel,
  StructureTabLabel
} from "../../components/table-detail/tableDetailTabLabels";
import { Alert } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { EmptyState } from "../../components/ui/empty-state";
import { PageHeader } from "../../components/ui/page-header";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../components/ui/tabs";
import { formatTime } from "../../lib/format";
import { TableDetailProvider } from "./TableDetailContext";
import { useTableDetail } from "./useTableDetail";

const OverviewTabPanel = lazy(() =>
  import("../../components/table-detail/OverviewTabPanel").then((m) => ({ default: m.OverviewTabPanel }))
);
const StructureTabPanel = lazy(() =>
  import("../../components/table-detail/StructureTabPanel").then((m) => ({ default: m.StructureTabPanel }))
);
const HealthQualityTabPanel = lazy(() =>
  import("../../components/table-detail/HealthQualityTabPanel").then((m) => ({ default: m.HealthQualityTabPanel }))
);
const CostTabPanel = lazy(() =>
  import("../../components/table-detail/CostTabPanel").then((m) => ({ default: m.CostTabPanel }))
);
const LineageTabPanel = lazy(() =>
  import("../../components/table-detail/LineageTabPanel").then((m) => ({ default: m.LineageTabPanel }))
);
const ActivityTabPanel = lazy(() =>
  import("../../components/table-detail/ActivityTabPanel").then((m) => ({ default: m.ActivityTabPanel }))
);

const TabFallback = () => (
  <AsyncBoundary isLoading skeleton={null}>
    <span />
  </AsyncBoundary>
);

const lazyPanel = (Panel: React.LazyExoticComponent<React.ComponentType>) => (
  <Suspense fallback={<TabFallback />}>
    <Panel />
  </Suspense>
);

const tabTriggerClass =
  "relative rounded-none px-4 py-2 after:pointer-events-none after:absolute after:inset-x-0 after:bottom-0 after:h-0.5 after:bg-transparent after:content-[''] data-[state=active]:after:bg-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none";

export const TableDetailShell = () => {
  const ctx = useTableDetail();
  const {
    row,
    table,
    navigate,
    stats,
    sourceName,
    lastSnapshotCommittedAt,
    score,
    savingsDisplay,
    reclaimableBytes,
    partitionColLabel,
    hideSinglePartitionKpi,
    scan,
    queuedScanRunId,
    handleScanAction,
    silentLoss,
    repairSilentLoss,
    activeTab,
    handleTabChange,
    previewOpen,
    setPreviewOpen,
    settingsOpen,
    setSettingsOpen
  } = ctx;

  if (!row && !table.isLoading) {
    return (
      <div className="table-detail">
        <PageHeader
          title="Table not found"
          subtitle="It may not have been scanned yet, was removed from the catalog, or this link is out of date."
        />
        <EmptyState
          title="This table couldn't be found"
          description="Search the catalog to find it under a different source or schema, or go back to Datalake health to see what's currently tracked."
          action={
            <div className="flex flex-wrap justify-center gap-2">
              <Link to="/catalogs">
                <Button variant="primary">Search catalogs</Button>
              </Link>
              <Link to="/">
                <Button variant="secondary">Go to Datalake health</Button>
              </Link>
            </div>
          }
        />
      </div>
    );
  }

  return (
    <TableDetailProvider value={ctx}>
      <div className="table-detail">
        <AssetHeader
          navigate={navigate}
          row={row}
          stats={stats}
          sourceName={sourceName}
          lastSnapshotCommittedAt={lastSnapshotCommittedAt}
          score={score}
          savingsDisplay={savingsDisplay}
          reclaimableBytes={reclaimableBytes}
          partitionColLabel={partitionColLabel}
          hideSinglePartitionKpi={hideSinglePartitionKpi}
          scan={scan}
          queuedScanRunId={queuedScanRunId}
          handleScanAction={handleScanAction}
        />
        <EngineActionStrip onOpenSettings={() => setSettingsOpen(true)} />

        <div className="td-main-column">
          {silentLoss.data && !silentLoss.data.is_clean ? (
            <Alert
              variant="error"
              className="mb-4"
              title="Silent data loss detected"
              description={
                <div className="flex flex-col gap-1.5">
                  <span>
                    {silentLoss.data.orphan_data_files.length} orphan data file(s) totaling{" "}
                    {(silentLoss.data.orphan_size_bytes / 1024 / 1024).toFixed(1)} MB outside any committed
                    snapshot. Root cause: {silentLoss.data.root_cause}. Detected {formatTime(silentLoss.data.detected_at)}.
                  </span>
                  <span className="text-xs text-muted-foreground">
                    Known scope: this only checks the files Cryo already knows about from the last scan; it does not
                    rewrite manifests and cannot delete or commit anything from this page.
                  </span>
                  <ConfirmAction
                    title="Run a Spark dry-run check?"
                    description={`Dispatches a Spark-on-K8s job that lists ${silentLoss.data.orphan_data_files.length} suspected orphan data file(s) (${(silentLoss.data.orphan_size_bytes / 1024 / 1024).toFixed(1)} MB) against S3 storage to confirm they're really orphaned. This is a dry run: it does not rewrite manifests, and it does not delete or commit anything -- it only reports what it finds. Requires a configured Spark-on-K8s fallback destination for this table's source. Committing an actual repair is not available from this UI.`}
                    okText="Run dry-run check"
                    onConfirm={() => repairSilentLoss.mutate()}
                  >
                    <Button
                      size="sm"
                      variant="primary"
                      loading={repairSilentLoss.isPending}
                    >
                      Run Spark dry-run check
                    </Button>
                  </ConfirmAction>
                </div>
              }
            />
          ) : null}

          <Tabs value={activeTab} onValueChange={handleTabChange} className="td-main-tabs">
            <TabsList className="mb-5 h-auto w-full justify-start rounded-none border-b border-[var(--line,#e1e6ed)] bg-transparent p-0">
              <TabsTrigger value="overview" className={tabTriggerClass}>
                <OverviewTabLabel />
              </TabsTrigger>
              <TabsTrigger value="structure" className={tabTriggerClass}>
                <StructureTabLabel />
              </TabsTrigger>
              <TabsTrigger value="health-quality" className={tabTriggerClass}>
                <HealthQualityTabLabel />
              </TabsTrigger>
              <TabsTrigger value="cost" className={tabTriggerClass}>
                <CostTabLabel />
              </TabsTrigger>
              <TabsTrigger value="lineage" className={tabTriggerClass}>
                <LineageTabLabel />
              </TabsTrigger>
              <TabsTrigger value="activity" className={tabTriggerClass}>
                <ActivityTabLabel />
              </TabsTrigger>
            </TabsList>
            <TabsContent value="overview" className="td-tab-content mt-0">
              {lazyPanel(OverviewTabPanel)}
            </TabsContent>
            <TabsContent value="structure" className="td-tab-content mt-0">
              {lazyPanel(StructureTabPanel)}
            </TabsContent>
            <TabsContent value="health-quality" className="td-tab-content mt-0">
              {lazyPanel(HealthQualityTabPanel)}
            </TabsContent>
            <TabsContent value="cost" className="td-tab-content mt-0">
              {lazyPanel(CostTabPanel)}
            </TabsContent>
            <TabsContent value="lineage" className="td-tab-content mt-0">
              {lazyPanel(LineageTabPanel)}
            </TabsContent>
            <TabsContent value="activity" className="td-tab-content mt-0">
              {lazyPanel(ActivityTabPanel)}
            </TabsContent>
          </Tabs>
        </div>

        <PlanPreview open={previewOpen} tables={row ? [row] : []} onClose={() => setPreviewOpen(false)} />
        <TableSettingsSheet open={settingsOpen} onOpenChange={setSettingsOpen} />
      </div>
    </TableDetailProvider>
  );
};
