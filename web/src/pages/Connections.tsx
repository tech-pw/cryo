import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Alert } from "../components/ui/alert";
import { PageHeader } from "../components/ui/page-header";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import { AiProvidersTab, AiProvidersTabLabel } from "./connections/AiProvidersTab";
import { CatalogLatencyModal } from "./connections/CatalogLatencyModal";
import { ConnectionDrawers } from "./connections/ConnectionDrawers";
import { ConnectionsKpiRow } from "./connections/ConnectionsKpiRow";
import { ConnectionsProvider, useConnectionsContext } from "./connections/ConnectionsContext";
import { DestinationTestResultPanel } from "./connections/DestinationTestResultPanel";
import { EnginesTab } from "./connections/EnginesTab";
import { LakehousesTab } from "./connections/LakehousesTab";
import { OpsTab, OpsTabLabel } from "./connections/OpsTab";
import { SlackTab, SlackTabLabel } from "./connections/SlackTab";

export { formatSourceTestSuccessMessage } from "./connections/helpers";

const CONNECTIONS_TAB_KEYS = ["sources", "destinations", "ai-providers", "slack", "ops"] as const;
type ConnectionsTabKey = (typeof CONNECTIONS_TAB_KEYS)[number];
const DEFAULT_CONNECTIONS_TAB: ConnectionsTabKey = "sources";

const isConnectionsTabKey = (value: string | null, isAdmin: boolean): value is ConnectionsTabKey =>
  value !== null &&
  (CONNECTIONS_TAB_KEYS as readonly string[]).includes(value) &&
  (value !== "ops" || isAdmin);

const ConnectionsTabs = () => {
  const { role, testResult, setTestResult } = useConnectionsContext();
  const isAdmin = role === "admin";
  const [searchParams, setSearchParams] = useSearchParams();
  const urlTab = searchParams.get("tab");
  const tabFromUrl: ConnectionsTabKey = isConnectionsTabKey(urlTab, isAdmin) ? urlTab : DEFAULT_CONNECTIONS_TAB;
  const [activeTab, setActiveTab] = useState<ConnectionsTabKey>(tabFromUrl);

  useEffect(() => {
    setActiveTab(tabFromUrl);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tabFromUrl]);

  const handleTabChange = useCallback(
    (key: string) => {
      if (!isConnectionsTabKey(key, isAdmin)) return;
      setActiveTab(key);
      const next = new URLSearchParams(searchParams);
      if (key === DEFAULT_CONNECTIONS_TAB) {
        next.delete("tab");
      } else {
        next.set("tab", key);
      }
      setSearchParams(next, { replace: true });
    },
    [isAdmin, searchParams, setSearchParams]
  );

  return (
    <>
      {testResult ? <DestinationTestResultPanel result={testResult} onDismiss={() => setTestResult(null)} /> : null}
      <Tabs value={activeTab} onValueChange={handleTabChange}>
        <TabsList>
          <TabsTrigger value="sources">Lakehouses</TabsTrigger>
          <TabsTrigger value="destinations">Engines</TabsTrigger>
          <TabsTrigger value="ai-providers">
            <AiProvidersTabLabel />
          </TabsTrigger>
          <TabsTrigger value="slack">
            <SlackTabLabel />
          </TabsTrigger>
          {isAdmin ? (
            <TabsTrigger value="ops">
              <OpsTabLabel />
            </TabsTrigger>
          ) : null}
        </TabsList>
        <TabsContent value="sources">{activeTab === "sources" ? <LakehousesTab /> : null}</TabsContent>
        <TabsContent value="destinations">{activeTab === "destinations" ? <EnginesTab /> : null}</TabsContent>
        <TabsContent value="ai-providers">{activeTab === "ai-providers" ? <AiProvidersTab /> : null}</TabsContent>
        <TabsContent value="slack">{activeTab === "slack" ? <SlackTab /> : null}</TabsContent>
        {isAdmin ? (
          <TabsContent value="ops">{activeTab === "ops" ? <OpsTab /> : null}</TabsContent>
        ) : null}
      </Tabs>
    </>
  );
};

const ConnectionsBody = () => (
  <>
    <ConnectionsTabs />
    <CatalogLatencyModal />
    <ConnectionDrawers />
  </>
);

export const Connections = () => (
  <div>
    <PageHeader
      title="Connections"
      subtitle="Lakehouses are where your Iceberg data lives. Engines are what actually runs maintenance on it."
    />
    <Alert
      className="page-alert"
      variant="info"
      title="Recommended setup flow: Connect lakehouse -> Test connection -> Run first scan -> Add engine -> Assign preset."
    />
    <ConnectionsProvider>
      <ConnectionsKpiRow />
      <ConnectionsBody />
    </ConnectionsProvider>
  </div>
);
