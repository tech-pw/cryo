import { GitBranch, Wrench } from "lucide-react";
import { lazy, Suspense } from "react";
import { Link } from "react-router-dom";
import { PlanPreview } from "../../components/PlanPreview";
import { ConfirmAction } from "../../components/confirm";
import { TableDetailHeaderBar } from "../../components/table-detail/header/TableDetailHeaderBar";
import { TableSettingsSheet } from "../../components/table-detail/TableSettingsSheet";
import { AsyncBoundary } from "../../components/AsyncBoundary";
import {
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
import { useWriteControl } from "../../lib/useWriteControl";
import { TableDetailProvider } from "./TableDetailContext";
import { useTableDetail } from "./useTableDetail";

const OverviewTabPanel = lazy(() =>
  import("../../components/table-detail/overview/OverviewPanel").then((m) => ({ default: m.OverviewPanel }))
);
const StructureTabPanel = lazy(() =>
  import("../../components/table-detail/files/FilesTabPanel").then((m) => ({ default: m.FilesTabPanel }))
);
const HealthQualityTabPanel = lazy(() =>
  import("../../components/table-detail/quality/QualityTabPanel").then((m) => ({ default: m.QualityTabPanel }))
);
const CostTabPanel = lazy(() =>
  import("../../components/table-detail/cost/TableCostPanel").then((m) => ({ default: m.TableCostPanel }))
);
const LineageTabPanel = lazy(() =>
  import("../../components/table-detail/usage/UsageTabPanel").then((m) => ({ default: m.UsageTabPanel }))
);
const SnapshotsTabPanel = lazy(() =>
  import("./SnapshotsTab").then((m) => ({ default: m.SnapshotsTab }))
);
const MaintenanceTabPanel = lazy(() =>
  import("./MaintenanceTab").then((m) => ({ default: m.MaintenanceTab }))
);

const TabFallback = () => (
  <AsyncBoundary isLoading skeleton={null}>
    <span />
  </AsyncBoundary>
);

const lazyPanel = <P extends object>(
  Panel: React.LazyExoticComponent<React.ComponentType<P>>,
  props?: P
) => (
  <Suspense fallback={<TabFallback />}>
    <Panel {...((props ?? {}) as P)} />
  </Suspense>
);

const tabTriggerClass =
  "relative rounded-none px-4 py-2 after:pointer-events-none after:absolute after:inset-x-0 after:bottom-0 after:h-0.5 after:bg-transparent after:content-[''] data-[state=active]:after:bg-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none";

const SnapshotsTabLabel = () => (
  <span>
    <GitBranch className="mr-1 inline size-3.5" />
    Snapshots
  </span>
);

const MaintenanceTabLabel = () => (
  <span>
    <Wrench className="mr-1 inline size-3.5" />
    Maintenance
  </span>
);

export const TableDetailShell = () => {
  const ctx = useTableDetail();
  const dryRunAccess = useWriteControl("silentLossDryRun");
  const runAccess = useWriteControl("runMaintenance");
  const { row, table, silentLoss, repairSilentLoss, previewOpen, setPreviewOpen, settingsOpen, setSettingsOpen } =
    ctx;

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

  const activeTab = ctx.activeTab;
  const handleTabChange = (tabKey: string) => {
    ctx.handleTabChange(tabKey);
  };

  return (
    <TableDetailProvider value={ctx}>
      <div className="table-detail">
        <TableDetailHeaderBar />

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
                      disabled={!dryRunAccess.allowed}
                      title={dryRunAccess.reason ?? undefined}
                    >
                      Run Spark dry-run check
                    </Button>
                  </ConfirmAction>
                </div>
              }
            />
          ) : null}

          <Tabs value={activeTab} onValueChange={handleTabChange} className="td-main-tabs">
            <label className="mb-3 block md:hidden">
              <span className="sr-only">Table section</span>
              <select
                className="h-[var(--control-height)] w-full rounded-[var(--radius-default)] border border-border bg-background px-3 text-sm"
                value={activeTab}
                onChange={(event) => handleTabChange(event.target.value)}
              >
                <option value="overview">Overview</option>
                <option value="files">Schema & files</option>
                <option value="snapshots">Snapshots</option>
                <option value="maintenance">Maintenance</option>
                <option value="usage">Usage</option>
                <option value="quality">Quality</option>
                <option value="cost">Cost</option>
              </select>
            </label>
            <TabsList className="mb-5 hidden h-auto w-full justify-start rounded-none border-b border-[var(--line,#e1e6ed)] bg-transparent p-0 md:flex">
              <TabsTrigger value="overview" className={tabTriggerClass}>
                <OverviewTabLabel />
              </TabsTrigger>
              <TabsTrigger value="files" className={tabTriggerClass}>
                <StructureTabLabel />
              </TabsTrigger>
              <TabsTrigger value="snapshots" className={tabTriggerClass}>
                <SnapshotsTabLabel />
              </TabsTrigger>
              <TabsTrigger value="maintenance" className={tabTriggerClass}>
                <MaintenanceTabLabel />
              </TabsTrigger>
              <TabsTrigger value="usage" className={tabTriggerClass}>
                <LineageTabLabel />
              </TabsTrigger>
              <TabsTrigger value="quality" className={tabTriggerClass}>
                <HealthQualityTabLabel />
              </TabsTrigger>
              <TabsTrigger value="cost" className={tabTriggerClass}>
                <CostTabLabel />
              </TabsTrigger>
            </TabsList>
            <TabsContent value="overview" className="td-tab-content mt-0">
              {lazyPanel(OverviewTabPanel)}
            </TabsContent>
            <TabsContent value="files" className="td-tab-content mt-0">
              {lazyPanel(StructureTabPanel)}
            </TabsContent>
            <TabsContent value="snapshots" className="td-tab-content mt-0">
              {lazyPanel(SnapshotsTabPanel)}
            </TabsContent>
            <TabsContent value="maintenance" className="td-tab-content mt-0">
              {lazyPanel(MaintenanceTabPanel)}
            </TabsContent>
            <TabsContent value="usage" className="td-tab-content mt-0">
              <Suspense fallback={<TabFallback />}>
                <LineageTabPanel tableId={ctx.tableId} />
              </Suspense>
            </TabsContent>
            <TabsContent value="quality" className="td-tab-content mt-0">
              <Suspense fallback={<TabFallback />}>
                <HealthQualityTabPanel tableId={ctx.tableId} />
              </Suspense>
            </TabsContent>
            <TabsContent value="cost" className="td-tab-content mt-0">
              <Suspense fallback={<TabFallback />}>
                <CostTabPanel tableId={ctx.tableId} />
              </Suspense>
            </TabsContent>
          </Tabs>
        </div>

        <PlanPreview
          open={previewOpen}
          tables={row ? [row] : []}
          onClose={() => setPreviewOpen(false)}
          onRunTable={
            runAccess.allowed
              ? async () => {
                  const result = await ctx.run.mutateAsync();
                  return { run_id: result.run_id };
                }
              : undefined
          }
          runDisabledReason={runAccess.reason}
        />
        <TableSettingsSheet open={settingsOpen} onOpenChange={setSettingsOpen} />
      </div>
    </TableDetailProvider>
  );
};
