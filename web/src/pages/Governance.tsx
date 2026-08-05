import { AlertTriangle, Database, Radar, Shield } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Alert } from "../components/ui/alert";
import { PageHeader } from "../components/ui/page-header";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import { AttentionTab } from "./governance/AttentionTab";
import {
  DEFAULT_GOVERNANCE_TAB,
  isGovernanceTabKey,
  resolveGovernanceTab,
  type GovernanceTabKey
} from "./governance/constants";
import { GovernanceKpiRow } from "./governance/GovernanceKpiRow";
import { IntelligenceTab } from "./governance/IntelligenceTab";
import { InventoryTab } from "./governance/InventoryTab";
import { PoliciesTab } from "./governance/PoliciesTab";

const TABS: Array<{
  key: GovernanceTabKey;
  label: string;
  icon: React.ReactNode;
}> = [
  { key: "triage", label: "Triage", icon: <AlertTriangle className="size-3.5" aria-hidden="true" /> },
  { key: "access", label: "Access & policy", icon: <Shield className="size-3.5" aria-hidden="true" /> },
  { key: "signals", label: "Signals & lineage", icon: <Radar className="size-3.5" aria-hidden="true" /> },
  { key: "ownership", label: "Ownership & inventory", icon: <Database className="size-3.5" aria-hidden="true" /> }
];

const tabContent = (key: GovernanceTabKey) => {
  switch (key) {
    case "triage":
      return <AttentionTab />;
    case "access":
      return <PoliciesTab />;
    case "signals":
      return <IntelligenceTab />;
    case "ownership":
      return <InventoryTab />;
  }
};

export const Governance = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const urlTab = searchParams.get("tab");
  const tabFromUrl: GovernanceTabKey = resolveGovernanceTab(urlTab);
  const [activeTab, setActiveTab] = useState<GovernanceTabKey>(tabFromUrl);

  useEffect(() => {
    setActiveTab(tabFromUrl);
  }, [tabFromUrl]);

  // Legacy `?tab=` values (e.g. `attention`, `policies`) resolve correctly above but should be
  // rewritten to the current task-oriented key so a shared/bookmarked link normalizes on next
  // navigation instead of perpetuating the old value.
  useEffect(() => {
    if (urlTab !== null && !isGovernanceTabKey(urlTab)) {
      const next = new URLSearchParams(searchParams);
      if (tabFromUrl === DEFAULT_GOVERNANCE_TAB) {
        next.delete("tab");
      } else {
        next.set("tab", tabFromUrl);
      }
      setSearchParams(next, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [urlTab]);

  const handleTabChange = useCallback(
    (key: string) => {
      if (!isGovernanceTabKey(key)) return;
      setActiveTab(key);
      const next = new URLSearchParams(searchParams);
      if (key === DEFAULT_GOVERNANCE_TAB) {
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
        title="Governance"
        subtitle="Approvals, anomalies, FinOps forecast, tier policies, and table lineage surfaced from the advanced API."
      />
      <Alert
        className="mb-4"
        variant="warning"
        title="Start with urgent signals first: pending approvals, broken tables, and active anomalies."
      />

      <GovernanceKpiRow />

      <Tabs value={activeTab} onValueChange={handleTabChange}>
        <TabsList>
          {TABS.map((tab) => (
            <TabsTrigger key={tab.key} value={tab.key} className="gap-1.5">
              {tab.icon}
              {tab.label}
            </TabsTrigger>
          ))}
        </TabsList>
        {TABS.map((tab) => (
          <TabsContent key={tab.key} value={tab.key}>
            {activeTab === tab.key ? tabContent(tab.key) : null}
          </TabsContent>
        ))}
      </Tabs>
    </div>
  );
};
