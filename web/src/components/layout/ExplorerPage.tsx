import { Space } from "antd";
import type { ReactNode } from "react";
import { PageHeader, QueryErrorAlert } from "../common";

interface ExplorerPageProps {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  error?: unknown;
  errorTitle?: string;
  filters?: ReactNode;
  bulkBar?: ReactNode;
  children: ReactNode;
}

export const ExplorerPage = ({
  title,
  subtitle,
  actions,
  error,
  errorTitle,
  filters,
  bulkBar,
  children
}: ExplorerPageProps) => (
  <div>
    <PageHeader title={title} subtitle={subtitle} actions={actions} />
    <QueryErrorAlert error={error} title={errorTitle ?? "Failed to load explorer data"} />
    <Space orientation="vertical" className="full-width" size={12}>
      {filters}
      {bulkBar}
      {children}
    </Space>
  </div>
);
