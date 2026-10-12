import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { PageHeader } from "../components/common";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import { Impact } from "./Impact";
import { ScanCost } from "./ScanCost";
import { AwsIntegrationsTab } from "./cost/AwsIntegrationsTab";
import { CostOverviewTab } from "./cost/CostOverviewTab";
import { S3RequestsTab } from "./cost/S3RequestsTab";

/**
 * Single consolidated Cost page.
 *
 * Replaces:
 *   - `/cost`        (was CostSavings)        -> "Overview" tab
 *   - `/aws`         (was AwsIntegrations)    -> "AWS integrations" tab
 *   - `/cost-insights`                         -> "S3 requests" tab
 *
 * `/attribution` stays hidden and intentionally out of nav until tag-based team
 * attribution is production-ready.
 *
 * The page header + subtitle lives once at the top of the page; each tab adds its
 * own KPI strip, charts, and tables. Tab keys map to URL `?tab=` so the active tab is
 * shareable (matches the existing pattern in web/src/components/common/Tabs.tsx).
 */
type CostTabKey = "storage" | "requests" | "impact" | "reads" | "aws";

const DEFAULT_TAB: CostTabKey = "storage";

const TAB_ALIASES: Record<string, CostTabKey> = {
  overview: "storage",
  storage: "storage",
  "s3-requests": "requests",
  requests: "requests",
  impact: "impact",
  reads: "reads",
  "aws-integrations": "aws",
  aws: "aws"
};

const TABS: Array<{
  key: CostTabKey;
  label: string;
  subtitle: string;
}> = [
  {
    key: "storage",
    label: "Storage",
    subtitle: "What we pay for storage, and what maintenance would change."
  },
  {
    key: "impact",
    label: "Impact",
    subtitle: "What maintenance already changed."
  },
  {
    key: "reads",
    label: "Reads",
    subtitle: "Who is scanning data. Bytes, not dollars."
  },
  {
    key: "requests",
    label: "Requests",
    subtitle: "What S3 API calls cost."
  },
  {
    key: "aws",
    label: "AWS",
    subtitle: "Which lakehouses have a storage-metrics connection."
  }
];

const isTabKey = (value: string | null): value is CostTabKey => value != null && value in TAB_ALIASES;

export const Cost = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const urlTab = searchParams.get("tab");
  const tabFromUrl: CostTabKey = isTabKey(urlTab) ? TAB_ALIASES[urlTab] : DEFAULT_TAB;
  const [activeTab, setActiveTab] = useState<CostTabKey>(tabFromUrl);
  const [headerActions, setHeaderActions] = useState<ReactNode>(null);

  useEffect(() => {
    setActiveTab(tabFromUrl);
  }, [tabFromUrl]);

  const handleTabChange = useCallback(
    (key: string) => {
      if (!isTabKey(key)) return;
      setActiveTab(key);
      setHeaderActions(null);
      const next = new URLSearchParams(searchParams);
      if (key === DEFAULT_TAB) {
        next.delete("tab");
      } else {
        next.set("tab", key);
      }
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams]
  );

  const active = TABS.find((tab) => tab.key === activeTab) ?? TABS[0];

  return (
    <div>
      <PageHeader title="Cost" subtitle={active.subtitle} actions={headerActions} />
      <Tabs value={activeTab} onValueChange={handleTabChange}>
        <TabsList>
          {TABS.map((tab) => (
            <TabsTrigger key={tab.key} value={tab.key}>
              {tab.label}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="storage">
          <CostOverviewTab active={activeTab === "storage"} onHeaderActions={setHeaderActions} />
        </TabsContent>
        <TabsContent value="requests">
          <S3RequestsTab />
        </TabsContent>
        <TabsContent value="impact">
          <Impact embedded />
        </TabsContent>
        <TabsContent value="reads">
          <ScanCost embedded />
        </TabsContent>
        <TabsContent value="aws">
          <AwsIntegrationsTab active={activeTab === "aws"} onHeaderActions={setHeaderActions} />
        </TabsContent>
      </Tabs>
    </div>
  );
};
