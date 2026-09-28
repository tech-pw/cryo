import { type JSX } from "react";
import { ChartCard } from "./ChartCard";
import { formatFixedUnit } from "./format";
import { plotValue } from "./plotValue";

function sideText(value: number | null, unit: string): string {
  const plotted = plotValue(value);
  if (plotted == null) return "not reported";
  return formatFixedUnit(plotted, unit);
}

export const BeforeAfter = ({
  label,
  before,
  after,
  unit,
  bytesUnreported = false
}: {
  label: string;
  before: number | null;
  after: number | null;
  unit: string;
  bytesUnreported?: boolean;
}): JSX.Element => {
  const beforeValue = plotValue(before);
  const afterValue = plotValue(after);
  return (
    <ChartCard
      title={label}
      subtitle={{ unit }}
      table={[
        {
          id: "before",
          side: "Before",
          value: beforeValue,
          ...(bytesUnreported ? { bytes: null } : {})
        },
        {
          id: "after",
          side: "After",
          value: afterValue,
          ...(bytesUnreported ? { bytes: null } : {})
        }
      ]}
      columns={[
        { key: "side", header: "Side" },
        { key: "value", header: unit, align: "right" },
        ...(bytesUnreported ? [{ key: "bytes", header: "Bytes", align: "right" as const }] : [])
      ]}
    >
      <p data-testid="before-after" className="text-sm text-foreground">
        <span data-testid="before-after-before">{sideText(before, unit)}</span>
        <span aria-hidden="true"> → </span>
        <span data-testid="before-after-after">{sideText(after, unit)}</span>
      </p>
      {bytesUnreported ? (
        <p data-testid="before-after-bytes" className="mt-1 text-sm text-muted-foreground">
          <span className="sr-only">Bytes </span>
          <span>not reported</span>
        </p>
      ) : null}
    </ChartCard>
  );
};
