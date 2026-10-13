import { Cpu, Database, Layers } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useLocation, useSearchParams } from "react-router-dom";
import { Alert } from "../components/ui/alert";
import { PageHeader } from "../components/ui/page-header";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import { AiProvidersTab, AiProvidersTabLabel } from "./connections/AiProvidersTab";
import { CapabilitiesTab } from "./connections/CapabilitiesTab";
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

const CONNECTIONS_TAB_KEYS = [
  "sources",
  "destinations",
  "capabilities",
  "ai-providers",
  "slack",
  "ops"
] as const;
type ConnectionsTabKey = (typeof CONNECTIONS_TAB_KEYS)[number];
const DEFAULT_CONNECTIONS_TAB: ConnectionsTabKey = "sources";

const aliasConnectionsTab = (value: string | null): string | null => {
  if (value === "lakehouses") return "sources";
  if (value === "engines") return "destinations";
  return value;
};

const isConnectionsTabKey = (value: string | null, isAdmin: boolean): value is ConnectionsTabKey =>
  value !== null &&
  (CONNECTIONS_TAB_KEYS as readonly string[]).includes(value) &&
  (value !== "ops" || isAdmin);

const ConnectionsTabs = ({ forcedTab }: { forcedTab?: ConnectionsTabKey }) => {
  const { role, testResult, setTestResult } = useConnectionsContext();
  const { pathname } = useLocation();
  const isAdmin = role === "admin";
  const onSettings = pathname.startsWith("/settings");
  const [searchParams, setSearchParams] = useSearchParams();
  const urlTab = forcedTab ?? aliasConnectionsTab(searchParams.get("tab"));
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
      const written = onSettings
        ? key === "sources"
          ? "lakehouses"
          : key === "destinations"
            ? "engines"
            : key
        : key;
      if ((!onSettings && written === DEFAULT_CONNECTIONS_TAB) || (onSettings && written === "lakehouses")) {
        next.delete("tab");
      } else {
        next.set("tab", written);
      }
      setSearchParams(next, { replace: true });
    },
    [isAdmin, onSettings, searchParams, setSearchParams]
  );

  return (
    <>
      {testResult ? <DestinationTestResultPanel result={testResult} onDismiss={() => setTestResult(null)} /> : null}
      <Tabs value={activeTab} onValueChange={handleTabChange}>
        {forcedTab ? null : (
        <TabsList>
          <TabsTrigger value="sources">
            <span className="inline-flex items-center gap-1.5">
              <Database className="size-3.5" aria-hidden="true" />
              Lakehouses
            </span>
          </TabsTrigger>
          <TabsTrigger value="destinations">
            <span className="inline-flex items-center gap-1.5">
              <Cpu className="size-3.5" aria-hidden="true" />
              Engines
            </span>
          </TabsTrigger>
          <TabsTrigger value="capabilities">
            <span className="inline-flex items-center gap-1.5">
              <Layers className="size-3.5" aria-hidden="true" />
              Capabilities
            </span>
          </TabsTrigger>
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
        )}
        <TabsContent value="sources">{activeTab === "sources" ? <LakehousesTab /> : null}</TabsContent>
        <TabsContent value="destinations">{activeTab === "destinations" ? <EnginesTab /> : null}</TabsContent>
        <TabsContent value="capabilities">
          {activeTab === "capabilities" ? <CapabilitiesTab /> : null}
        </TabsContent>
        <TabsContent value="ai-providers">{activeTab === "ai-providers" ? <AiProvidersTab /> : null}</TabsContent>
        <TabsContent value="slack">{activeTab === "slack" ? <SlackTab /> : null}</TabsContent>
        {isAdmin ? (
          <TabsContent value="ops">{activeTab === "ops" ? <OpsTab /> : null}</TabsContent>
        ) : null}
      </Tabs>
    </>
  );
};

const ConnectionsBody = ({ forcedTab }: { forcedTab?: ConnectionsTabKey }) => (
  <>
    <ConnectionsTabs forcedTab={forcedTab} />
    <CatalogLatencyModal />
    <ConnectionDrawers />
  </>
);

export const Connections = ({
  embedded = false,
  forcedTab
}: {
  embedded?: boolean;
  forcedTab?: ConnectionsTabKey;
}) => (
  <div>
    {embedded ? null : (
      <>
        <PageHeader
          title="Connections"
          subtitle="Lakehouses are where your Iceberg data lives. Engines are what actually runs maintenance on it."
        />
        <Alert
          className="page-alert"
          variant="info"
          title="Recommended setup flow: Connect lakehouse -> Test connection -> Run first scan -> Add engine -> Assign preset."
        />
      </>
    )}
    <ConnectionsProvider>
      {embedded ? null : <ConnectionsKpiRow />}
      <ConnectionsBody forcedTab={forcedTab} />
    </ConnectionsProvider>
  </div>
);
