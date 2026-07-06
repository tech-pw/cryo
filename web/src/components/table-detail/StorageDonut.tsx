import { Pie, PieChart, ResponsiveContainer, Tooltip as RechartsTooltip, Cell } from "recharts";
import { Typography } from "antd";
import { formatBytesGb, formatCurrency } from "../../lib/format";
import { chartTooltipProps } from "../../lib/theme";

interface StorageDonutProps {
  liveBytes: number;
  deadBytes: number;
  metadataBytes: number;
  orphanBytes?: number | null;
  pricePerGbMonth: number;
}

const donutColors = ["#3d6fd4", "#e0b03d", "#8b5cf6", "#f0665a"];

export const StorageDonut = ({
  liveBytes,
  deadBytes,
  metadataBytes,
  orphanBytes,
  pricePerGbMonth
}: StorageDonutProps) => {
  const parts = [
    { key: "Live", bytes: liveBytes },
    { key: "Dead", bytes: deadBytes },
    { key: "Metadata", bytes: metadataBytes },
    { key: "Orphan", bytes: orphanBytes ?? 0 }
  ].filter((part) => part.bytes > 0);

  const total = parts.reduce((sum, part) => sum + part.bytes, 0);

  if (!total) {
    return <Typography.Text type="secondary">Storage breakdown unavailable.</Typography.Text>;
  }

  return (
    <div className="td-storage-donut-wrap">
      <div className="td-storage-donut-chart">
        <ResponsiveContainer>
          <PieChart>
            <Pie
              data={parts}
              dataKey="bytes"
              nameKey="key"
              innerRadius={48}
              outerRadius={72}
              paddingAngle={2}
              strokeWidth={1}
            >
              {parts.map((entry, index) => (
                <Cell key={entry.key} fill={donutColors[index % donutColors.length]} />
              ))}
            </Pie>
            <RechartsTooltip
              {...chartTooltipProps}
              formatter={(value, name) => {
                const bytes = Number(value ?? 0);
                const usd = ((bytes / (1024 ** 3)) * pricePerGbMonth) || 0;
                return [`${formatBytesGb(bytes)} · ${formatCurrency(usd)}/mo`, String(name)];
              }}
            />
          </PieChart>
        </ResponsiveContainer>
        <div className="td-storage-donut-center">
          <div className="td-storage-donut-total">{formatBytesGb(total)}</div>
          <div className="td-storage-donut-label">Total storage</div>
        </div>
      </div>
      <div className="td-storage-donut-legend">
        {parts.map((part, index) => {
          const usd = ((part.bytes / (1024 ** 3)) * pricePerGbMonth) || 0;
          return (
            <div key={part.key} className="td-storage-donut-legend-row">
              <span
                className="td-storage-donut-dot"
                style={{ background: donutColors[index % donutColors.length] }}
              />
              <span>{part.key}</span>
              <span>{formatBytesGb(part.bytes)}</span>
              <span>{formatCurrency(usd)}/mo</span>
            </div>
          );
        })}
      </div>
    </div>
  );
};
