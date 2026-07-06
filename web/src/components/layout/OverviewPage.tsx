import { Skeleton, Space } from "antd";
import type { ReactNode } from "react";
import { PageHeader, QueryErrorAlert } from "../common";

interface OverviewPageProps {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  loading?: boolean;
  error?: unknown;
  errorTitle?: string;
  preContent?: ReactNode;
  kpis?: ReactNode;
  children: ReactNode;
}

export const OverviewPage = ({
  title,
  subtitle,
  actions,
  loading,
  error,
  errorTitle,
  preContent,
  kpis,
  children
}: OverviewPageProps) => (
  <div>
    <PageHeader title={title} subtitle={subtitle} actions={actions} />
    <QueryErrorAlert error={error} title={errorTitle ?? "Failed to load page data"} />
    {loading ? (
      <Skeleton active />
    ) : (
      <Space orientation="vertical" className="full-width" size={12}>
        {preContent}
        {kpis}
        {children}
      </Space>
    )}
  </div>
);
