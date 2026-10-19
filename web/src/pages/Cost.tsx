import { BarChart3, Database, ScanSearch } from "lucide-react";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { PageHeader } from "../components/common";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import { ScanCost } from "./ScanCost";
import { CostOverviewTab } from "./cost/CostOverviewTab";
import { S3RequestsTab } from "./cost/S3RequestsTab";

/**
 * Single consolidated Cost page.
 *
 * Replaces:
 *   - `/cost`        (was CostSavings)        -> "Overview" tab
 *   - `/aws`         (was AwsIntegrations)    -> Connections, which owns the form
 *   - `/cost-insights`                         -> "S3 requests" tab
 *
 * `/attribution` stays hidden and intentionally out of nav until tag-based team
 * attribution is production-ready.
 *
 * The page header + subtitle lives once at the top of the page; each tab adds its
 * own KPI strip, charts, and tables. Tab keys map to URL `?tab=` so the active tab is
 * shareable (matches the existing pattern in web/src/components/common/Tabs.tsx).
 */
type CostTabKey = "storage" | "requests" | "reads";

const DEFAULT_TAB: CostTabKey = "storage";

const TAB_ALIASES: Record<string, CostTabKey> = {
  overview: "storage",
  storage: "storage",
  "s3-requests": "requests",
  requests: "requests",
  impact: "storage",
  reads: "reads",
  // The connection form moved to Connections. Old links land on Storage, which is
  // where the price those credentials produce is shown.
  "aws-integrations": "storage",
  aws: "storage"
};

const TABS: Array<{
  key: CostTabKey;
  label: string;
  subtitle: string;
  icon: ReactNode;
}> = [
  {
    key: "storage",
    label: "Storage",
    subtitle: "What we pay for storage, and what maintenance would change.",
    icon: <BarChart3 className="size-3.5" aria-hidden="true" />
  },
  {
    key: "requests",
    label: "Requests",
    subtitle: "What S3 API calls cost.",
    icon: <Database className="size-3.5" aria-hidden="true" />
  },
  {
    key: "reads",
    label: "Reads",
    subtitle: "Who is scanning data. Bytes, not dollars.",
    icon: <ScanSearch className="size-3.5" aria-hidden="true" />
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
            <TabsTrigger key={tab.key} value={tab.key} className="gap-1.5">
              {tab.icon}
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
        <TabsContent value="reads">
          <ScanCost embedded />
        </TabsContent>
      </Tabs>
    </div>
  );
};
