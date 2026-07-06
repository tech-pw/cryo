import { InfoCircleOutlined } from "@ant-design/icons";
import { Alert, Button, Empty, Space, Statistic, Tooltip, Typography } from "antd";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { ResponsiveContainer } from "recharts";
import { ApiError } from "../api/client";

export const ChartFrame = ({
  empty,
  emptyTitle = "No chart data",
  emptyDescription = "Run a scan to populate this panel.",
  ariaLabel,
  size = "md",
  children
}: {
  empty: boolean;
  emptyTitle?: string;
  emptyDescription?: string;
  ariaLabel?: string;
  /** "xs" is for small fixed-radius charts (e.g. a donut) that don't need a full-size frame. */
  size?: "md" | "xs";
  children: React.ReactNode;
}) =>
  empty ? (
    <EmptyState title={emptyTitle} description={emptyDescription} />
  ) : (
    <div className={size === "xs" ? "chart-frame xs" : "chart-frame"} role="img" aria-label={ariaLabel}>
      <ResponsiveContainer>{children}</ResponsiveContainer>
    </div>
  );

export const PageHeader = ({
  title,
  subtitle,
  actions
}: {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) => (
  <div className="page-header">
    <div>
      <Typography.Title level={2}>{title}</Typography.Title>
      {subtitle ? <Typography.Text type="secondary">{subtitle}</Typography.Text> : null}
    </div>
    {actions ? <Space wrap>{actions}</Space> : null}
  </div>
);

export const EmptyState = ({
  title,
  description,
  action
}: {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
}) => (
  <Empty
    className="empty-state"
    image={Empty.PRESENTED_IMAGE_SIMPLE}
    description={
      <Space direction="vertical" size={4}>
        <Typography.Text strong>{title}</Typography.Text>
        {description ? <Typography.Text type="secondary">{description}</Typography.Text> : null}
        {action}
      </Space>
    }
  />
);

export interface StatItem {
  label: string;
  value: ReactNode;
  suffix?: ReactNode;
  /** Shown as a hoverable info icon next to the label — use for any metric whose name alone
   * doesn't say what it measures (e.g. "why is this called that"). */
  tooltip?: string;
}

/** A label with a hoverable info icon — use for any field name that doesn't say what it means
 * on its own (e.g. "Live" bytes, "Precision coverage"). Reused by StatStrip and Descriptions. */
export const HintLabel = ({ label, tooltip }: { label: ReactNode; tooltip?: string }) =>
  tooltip ? (
    <Space size={4}>
      <span>{label}</span>
      <Tooltip title={tooltip}>
        <InfoCircleOutlined style={{ color: "var(--ink-faint, rgba(0,0,0,0.35))" }} />
      </Tooltip>
    </Space>
  ) : (
    <>{label}</>
  );

export const StatStrip = ({ items }: { items: StatItem[] }) => (
  <div className="stat-strip">
    {items.map((item) => (
      <Statistic
        key={item.label}
        title={<HintLabel label={item.label} tooltip={item.tooltip} />}
        value={String(item.value)}
        suffix={item.suffix}
      />
    ))}
  </div>
);

export const Sparkline = ({ values }: { values: Array<number | null | undefined> }) => {
  const clean = values.map((value) => (Number.isFinite(value) ? Number(value) : 0));
  const max = Math.max(...clean, 1);
  const width = 72;
  const height = 20;
  const step = clean.length > 1 ? width / (clean.length - 1) : width;
  const points = clean
    .map((value, index) => `${index * step},${height - (value / max) * (height - 2) - 1}`)
    .join(" ");

  return (
    <svg className="sparkline" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Small-file pressure">
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
};

export const JsonHelpButton = ({ value }: { value: unknown }) => (
  <Button size="small" onClick={() => navigator.clipboard.writeText(JSON.stringify(value, null, 2))}>
    Copy JSON
  </Button>
);

export const QueryErrorAlert = ({
  error,
  title = "Failed to load data",
  className = "page-alert"
}: {
  error: unknown;
  title?: string;
  className?: string;
}) => {
  if (!error) return null;
  const detail =
    error instanceof ApiError
      ? String(error.detail ?? error.message)
      : error instanceof Error
        ? error.message
        : "Unknown error";
  const isAuth = error instanceof ApiError && (error.status === 401 || error.status === 403);
  return (
    <Alert
      className={className}
      type="error"
      showIcon
      message={isAuth ? "Authentication or permission error" : title}
      description={
        <Space direction="vertical" size={4}>
          <Typography.Text>{detail}</Typography.Text>
          {isAuth ? (
            <Typography.Text type="secondary">
              Sign in again or switch to a role with sufficient permissions in the header.
            </Typography.Text>
          ) : null}
        </Space>
      }
    />
  );
};

export const TruncatedResultsBanner = ({
  shown,
  total,
  noun = "results"
}: {
  shown: number;
  total: number;
  noun?: string;
}) =>
  total > shown ? (
    <Alert
      className="page-alert"
      type="warning"
      showIcon
      message={`Showing ${shown.toLocaleString()} of ${total.toLocaleString()} ${noun}`}
      description="Refine filters or use search to narrow the list — the view is capped for performance."
    />
  ) : null;

export const FirstRunEmptyState = () => (
  <EmptyState
    title="No tables scanned yet"
    description="Connect a catalog source, then run a scan to populate health and cost metrics."
    action={
      <Link to="/connections">
        <Button type="primary">Connect a catalog</Button>
      </Link>
    }
  />
);
