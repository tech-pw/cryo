import { Alert, Space, Typography } from "antd";
import type { SourceNamespacesResult } from "../api/client";

export interface NamespaceErrorAlertProps {
  result?: SourceNamespacesResult;
  loading?: boolean;
  /** When true, the picker still showed options (sourced from previously-scanned tables). */
  fromScannedFallback?: boolean;
}

export const NamespaceErrorAlert = ({
  result,
  loading,
  fromScannedFallback = false
}: NamespaceErrorAlertProps) => {
  if (loading) return null;
  if (!result || result.ok) return null;
  return (
    <Alert
      type="warning"
      showIcon
      style={{ marginBottom: 8 }}
      message={
        <Space orientation="vertical" size={2}>
          <Typography.Text strong>
            Couldn&apos;t reach the catalog to list live schemas
          </Typography.Text>
          <Typography.Text type="secondary">{result.error}</Typography.Text>
          {fromScannedFallback ? (
            <Typography.Text type="secondary">
              Showing schemas Cryo has already scanned in this lakehouse. Trigger a full scan
              first to discover the rest.
            </Typography.Text>
          ) : null}
        </Space>
      }
    />
  );
};