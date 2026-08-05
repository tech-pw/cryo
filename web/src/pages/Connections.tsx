import { Alert } from "../components/ui/alert";
import { PageHeader } from "../components/ui/page-header";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import { AiProvidersTab, AiProvidersTabLabel } from "./connections/AiProvidersTab";
import { CapabilitiesTab } from "./connections/CapabilitiesTab";
import { CatalogLatencyModal } from "./connections/CatalogLatencyModal";
import { ConnectionDrawers } from "./connections/ConnectionDrawers";
import { ConnectionsKpiRow } from "./connections/ConnectionsKpiRow";
import { ConnectionsProvider, useConnectionsContext } from "./connections/ConnectionsContext";
import { EnginesTab } from "./connections/EnginesTab";
import { LakehousesTab } from "./connections/LakehousesTab";
import { OpsTab, OpsTabLabel } from "./connections/OpsTab";
import { SlackTab, SlackTabLabel } from "./connections/SlackTab";

export { formatSourceTestSuccessMessage } from "./connections/helpers";

const ConnectionsTabs = () => {
  const { role, testResult, setTestResult } = useConnectionsContext();

  return (
    <>
      {testResult ? (
        <Alert
          className="page-alert"
          variant={testResult.ok ? "success" : "warning"}
          title={testResult.ok ? "Engine capacity check passed" : "Engine capacity check returned warnings"}
          description={<pre className="inline-json mt-1 overflow-x-auto text-xs">{JSON.stringify(testResult, null, 2)}</pre>}
          onDismiss={() => setTestResult(null)}
        />
      ) : null}
      <Tabs defaultValue="sources">
        <TabsList>
          <TabsTrigger value="sources">Lakehouses</TabsTrigger>
          <TabsTrigger value="destinations">Engines</TabsTrigger>
          <TabsTrigger value="capabilities">Capabilities</TabsTrigger>
          <TabsTrigger value="ai-providers">
            <AiProvidersTabLabel />
          </TabsTrigger>
          <TabsTrigger value="slack">
            <SlackTabLabel />
          </TabsTrigger>
          {role === "admin" ? (
            <TabsTrigger value="ops">
              <OpsTabLabel />
            </TabsTrigger>
          ) : null}
        </TabsList>
        <TabsContent value="sources">
          <LakehousesTab />
        </TabsContent>
        <TabsContent value="destinations">
          <EnginesTab />
        </TabsContent>
        <TabsContent value="capabilities">
          <CapabilitiesTab />
        </TabsContent>
        <TabsContent value="ai-providers">
          <AiProvidersTab />
        </TabsContent>
        <TabsContent value="slack">
          <SlackTab />
        </TabsContent>
        {role === "admin" ? (
          <TabsContent value="ops">
            <OpsTab />
          </TabsContent>
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
