import { lazy, Suspense } from "react";
import { PlanPreview } from "../../components/PlanPreview";
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../components/ui/tabs";
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
    effectivePreset,
    effectiveDestination,
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
        <div className="td-breadcrumbs">
          <span className="td-breadcrumb-item" onClick={() => navigate("/")}>
            Home
          </span>
          <span className="td-breadcrumb-separator">/</span>
          <span className="td-breadcrumb-item">Catalogs</span>
        </div>
        <div className="td-identity-bar" style={{ marginTop: 8 }}>
          <div className="td-identity-info">
            <h3 className="m-0 text-xl font-bold text-foreground">Table not found</h3>
            <p className="mt-1 text-xs text-muted-foreground">
              The table may not have been scanned yet or has been removed.
            </p>
          </div>
        </div>
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
          effectivePreset={effectivePreset}
          effectiveDestination={effectiveDestination}
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
                <div className="flex flex-col gap-1">
                  <span>
                    {silentLoss.data.orphan_data_files.length} orphan data file(s) totaling{" "}
                    {(silentLoss.data.orphan_size_bytes / 1024 / 1024).toFixed(1)} MB outside any committed
                    snapshot. Root cause: {silentLoss.data.root_cause}.
                  </span>
                  <Button
                    size="sm"
                    variant="primary"
                    loading={repairSilentLoss.isPending}
                    onClick={() => repairSilentLoss.mutate()}
                  >
                    Repair (rewrite manifests)
                  </Button>
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
