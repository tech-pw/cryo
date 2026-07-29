import { BarChart3, Cloud, Database } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { PageHeader } from "../components/common";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import { CostOverviewTab } from "./cost/CostOverviewTab";
import { S3RequestsTab } from "./cost/S3RequestsTab";
import { AwsIntegrationsTab } from "./cost/AwsIntegrationsTab";

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
type CostTabKey = "overview" | "s3-requests" | "aws-integrations";

const DEFAULT_TAB: CostTabKey = "overview";

const TABS: Array<{
  key: CostTabKey;
  label: string;
  icon: React.ReactNode;
}> = [
  { key: "overview", label: "Overview", icon: <BarChart3 className="size-3.5" aria-hidden="true" /> },
  { key: "s3-requests", label: "S3 requests", icon: <Database className="size-3.5" aria-hidden="true" /> },
  { key: "aws-integrations", label: "AWS integrations", icon: <Cloud className="size-3.5" aria-hidden="true" /> }
];

const isTabKey = (value: string | null): value is CostTabKey =>
  value === "overview" || value === "s3-requests" || value === "aws-integrations";

export const Cost = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const urlTab = searchParams.get("tab");
  const tabFromUrl: CostTabKey = isTabKey(urlTab) ? urlTab : DEFAULT_TAB;
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
        subtitle="Storage, request-cost and AWS integration in one place. Each tab is self-contained -- jump between S3 request counts and storage cost without losing context."
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
        <TabsContent value="overview">
          <CostOverviewTab />
        </TabsContent>
        <TabsContent value="s3-requests">
          <S3RequestsTab />
        </TabsContent>
        <TabsContent value="aws-integrations">
          <AwsIntegrationsTab />
        </TabsContent>
      </Tabs>
    </div>
  );
};
