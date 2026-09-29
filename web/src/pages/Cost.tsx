import { BarChart3, Cloud, Database, LineChart, ScanSearch } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
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
  icon: React.ReactNode;
}> = [
  { key: "storage", label: "Storage", icon: <BarChart3 className="size-3.5" aria-hidden="true" /> },
  { key: "requests", label: "Requests", icon: <Database className="size-3.5" aria-hidden="true" /> },
  { key: "impact", label: "Impact", icon: <LineChart className="size-3.5" aria-hidden="true" /> },
  { key: "reads", label: "Reads", icon: <ScanSearch className="size-3.5" aria-hidden="true" /> },
  { key: "aws", label: "AWS", icon: <Cloud className="size-3.5" aria-hidden="true" /> }
];

const isTabKey = (value: string | null): value is CostTabKey => value != null && value in TAB_ALIASES;

export const Cost = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const urlTab = searchParams.get("tab");
  const tabFromUrl: CostTabKey = isTabKey(urlTab) ? TAB_ALIASES[urlTab] : DEFAULT_TAB;
  const [activeTab, setActiveTab] = useState<CostTabKey>(tabFromUrl);

  useEffect(() => {
    setActiveTab(tabFromUrl);
  }, [tabFromUrl]);

  const handleTabChange = useCallback(
    (key: string) => {
      if (!isTabKey(key)) return;
      setActiveTab(key);
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

  return (
    <div>
      <PageHeader
        title="Cost"
        subtitle="What do we pay, what did maintenance change, and who is reading?"
      />
      <Tabs value={activeTab} onValueChange={handleTabChange}>
        <TabsList>
          {TABS.map((tab) => (
            <TabsTrigger key={tab.key} value={tab.key} className="gap-1.5">
              {tab.icon}
              {tab.label}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="storage">
          <CostOverviewTab />
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
          <AwsIntegrationsTab />
        </TabsContent>
      </Tabs>
    </div>
  );
};
