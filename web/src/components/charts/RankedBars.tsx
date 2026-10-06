import { ChartCard } from "./ChartCard";
import { plotValue } from "./plotValue";
import type { ChartProvenance } from "./types";

export interface RankedBarItem {
  id: string;
  label: string;
  value: number | null;
}

export const RankedBars = ({
  title,
  unit,
  items,
  total,
  limit = 15,
  provenance = "measured"
}: {
  title: string;
  unit: string;
  items: RankedBarItem[];
  total?: number;
  limit?: number;
  provenance?: ChartProvenance;
}) => {
  const shown = items.slice(0, Math.max(0, limit));
  const max = Math.max(...shown.map((item) => plotValue(item.value) ?? 0), 0);
  const rows = shown.map((item) => ({
    id: item.id,
    name: item.label,
    value: plotValue(item.value) == null ? "Not measured" : String(item.value)
  }));

  return (
    <ChartCard
      title={title}
      provenance={provenance}
      subtitle={{ unit }}
      cap={total != null ? { shown: shown.length, total } : undefined}
      table={rows}
      columns={[
        { key: "name", header: "Name" },
        { key: "value", header: unit, align: "right" }
      ]}
    >
      <ol className="flex flex-col gap-1">
        {shown.map((item) => {
          const value = plotValue(item.value);
          const width = value == null || max <= 0 ? 0 : Math.max(2, (value / max) * 100);
          return (
            <li key={item.id} className="grid grid-cols-[minmax(0,1fr)_4rem] items-center gap-2 text-xs">
              <span className="truncate" title={item.label}>
                {item.label}
              </span>
              <span className="relative h-2 rounded-sm bg-[var(--color-surface-sunken)]">
                {value == null ? (
                  <span className="absolute inset-0 text-[10px] text-muted-foreground">Not measured</span>
                ) : (
                  <span
                    className="absolute inset-y-0 left-0 rounded-sm bg-[var(--color-primary)]"
                    style={{ width: `${width}%` }}
                  />
                )}
              </span>
            </li>
          );
        })}
      </ol>
    </ChartCard>
  );
};
