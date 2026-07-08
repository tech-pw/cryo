import { Tabs } from "antd";
import { BarChartOutlined, CloudOutlined, DatabaseOutlined } from "@ant-design/icons";
import { useCallback, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { PageHeader } from "../components/common";
import { CostOverviewTab } from "./cost/CostOverviewTab";
import { S3RequestsTab } from "./cost/S3RequestsTab";
import { AwsIntegrationsTab } from "./cost/AwsIntegrationsTab";

/**
 * Single consolidated Cost page.
 *
 * Replaces:
 *   - `/cost`        (was CostSavings)        -> "Overview" tab
 *   - `/aws`         (was AwsIntegrations)    -> "AWS integrations" tab
 *   - (new)                                    -> "S3 requests" tab
 *
 * `/attribution` and `/cost-insights` stay as-is -- they answer distinct
 * "team-split" / "ML forecasting" questions and aren't duplicates of these three.
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
  { key: "overview", label: "Overview", icon: <BarChartOutlined /> },
  { key: "s3-requests", label: "S3 requests", icon: <DatabaseOutlined /> },
  { key: "aws-integrations", label: "AWS integrations", icon: <CloudOutlined /> }
];

const isTabKey = (value: string | null): value is CostTabKey =>
  value === "overview" || value === "s3-requests" || value === "aws-integrations";

export const Cost = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const urlTab = searchParams.get("tab");
  const activeTab: CostTabKey = isTabKey(urlTab) ? urlTab : DEFAULT_TAB;

  const handleTabChange = useCallback(
    (key: string) => {
      if (!isTabKey(key)) return;
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
      <Tabs
        activeKey={activeTab}
        onChange={handleTabChange}
        items={TABS.map((tab) => ({
          key: tab.key,
          label: (
            <span>
              {tab.icon} {tab.label}
            </span>
          ),
          children:
            tab.key === "overview" ? (
              <CostOverviewTab />
            ) : tab.key === "s3-requests" ? (
              <S3RequestsTab />
            ) : (
              <AwsIntegrationsTab />
            )
        }))}
      />
    </div>
  );
};