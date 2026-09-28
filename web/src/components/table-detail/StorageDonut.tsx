import { Pie, PieChart, ResponsiveContainer, Tooltip as RechartsTooltip, Cell } from "recharts";
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
  const orphanKnown = orphanBytes != null && Number.isFinite(orphanBytes);
  const measuredParts = [
    { key: "Live", bytes: liveBytes },
    { key: "Dead", bytes: deadBytes },
    { key: "Metadata", bytes: metadataBytes },
    ...(orphanKnown ? [{ key: "Orphan", bytes: orphanBytes as number }] : [])
  ];
  const parts = measuredParts.filter((part) => part.bytes > 0);
  const measuredCount = orphanKnown ? 4 : 3;
  const total = measuredParts.reduce((sum, part) => sum + part.bytes, 0);
  const totalLabel =
    measuredCount < 4 ? `${formatBytesGb(total)} (3 of 4 measured)` : formatBytesGb(total);

  if (!total) {
    return (
      <span className="text-sm text-muted-foreground">
        {orphanKnown ? "Storage breakdown unavailable." : "Not measured"}
      </span>
    );
  }

  return (
    <div className="td-storage-donut-wrap">
      <div
        className="td-storage-donut-chart"
        role="img"
        aria-label={`Storage breakdown totaling ${totalLabel}: ${parts.map((part) => `${part.key} ${formatBytesGb(part.bytes)}`).join(", ")}${orphanKnown ? "" : ". Orphan not measured"}.`}
      >
        <ResponsiveContainer width="100%" height={168}>
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
                const bytes = typeof value === "number" && Number.isFinite(value) ? value : null;
                if (bytes == null) return ["Not measured", String(name)];
                const usd = (bytes / 1024 ** 3) * pricePerGbMonth;
                const usdLabel = Number.isFinite(usd) ? formatCurrency(usd) : "Cost data unavailable";
                return [`${formatBytesGb(bytes)} · ${usdLabel}/month`, String(name)];
              }}
            />
          </PieChart>
        </ResponsiveContainer>
        <div className="td-storage-donut-center">
          <div className="td-storage-donut-total">{totalLabel}</div>
          <div className="td-storage-donut-label">Total Storage</div>
        </div>
      </div>
      <div className="td-storage-donut-legend">
        {parts.map((part, index) => {
          const usd = (part.bytes / 1024 ** 3) * pricePerGbMonth;
          const usdLabel = Number.isFinite(usd) ? formatCurrency(usd) : "Cost data unavailable";
          return (
            <div key={part.key} className="td-storage-donut-legend-row">
              <span
                className="td-storage-donut-dot"
                style={{ background: donutColors[index % donutColors.length] }}
              />
              <span>{part.key}</span>
              <span>{formatBytesGb(part.bytes)}</span>
              <span>{usdLabel}/month</span>
            </div>
          );
        })}
        {orphanKnown ? null : (
          <div className="td-storage-donut-legend-row">
            <span className="td-storage-donut-dot" style={{ background: donutColors[3] }} />
            <span>Orphan</span>
            <span>Not measured</span>
          </div>
        )}
      </div>
    </div>
  );
};
