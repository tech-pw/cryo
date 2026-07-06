import { Space, Tabs, Typography } from "antd";
import type { ReactNode } from "react";
import { PageHeader } from "../common";

interface SettingsTab {
  key: string;
  label: ReactNode;
  intro?: ReactNode;
  content: ReactNode;
}

interface SettingsPageProps {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  activeKey: string;
  onChange: (key: string) => void;
  tabs: SettingsTab[];
}

export const SettingsPage = ({
  title,
  subtitle,
  actions,
  activeKey,
  onChange,
  tabs
}: SettingsPageProps) => (
  <div>
    <PageHeader title={title} subtitle={subtitle} actions={actions} />
    <Tabs
      activeKey={activeKey}
      onChange={onChange}
      items={tabs.map((tab) => ({
        key: tab.key,
        label: tab.label,
        children: (
          <Space orientation="vertical" className="full-width" size={12}>
            {tab.intro ? (
              <Typography.Text type="secondary" className="tab-intro">
                {tab.intro}
              </Typography.Text>
            ) : null}
            {tab.content}
          </Space>
        )
      }))}
    />
  </div>
);
