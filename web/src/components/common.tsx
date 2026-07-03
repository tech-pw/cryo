import { InfoCircleOutlined } from "@ant-design/icons";
import { Button, Empty, Space, Statistic, Tooltip, Typography } from "antd";
import type { ReactNode } from "react";
import { ResponsiveContainer } from "recharts";

export const ChartFrame = ({
  empty,
  emptyTitle = "No chart data",
  emptyDescription = "Run a scan to populate this panel.",
  children
}: {
  empty: boolean;
  emptyTitle?: string;
  emptyDescription?: string;
  children: React.ReactNode;
}) =>
  empty ? (
    <EmptyState title={emptyTitle} description={emptyDescription} />
  ) : (
    <div className="chart-frame">
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
