import { useMemo, useState } from "react";
import type { AssistantBlock, AssistantChartItem, AssistantColumn } from "../../../api/assistant";
import { RankedBars, RunLanes } from "../../charts";
import { Button } from "../../ui/button";
import { AssistantBlockTable } from "./RankedTableBlock";
import { EvidenceLinks } from "./EvidenceLinks";

const chartValue = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

const seriesItems = (block: Extract<AssistantBlock, { type: "chart" }>): AssistantChartItem[] => {
  if (block.items?.length) return block.items;
  return (block.series ?? []).map((item, index) => ({
    id: typeof item.id === "string" ? item.id : String(index),
    label: typeof item.label === "string" ? item.label : typeof item.name === "string" ? item.name : undefined,
    name: typeof item.name === "string" ? item.name : undefined,
    value: chartValue(item.value)
  }));
};

const tableRows = (block: Extract<AssistantBlock, { type: "chart" }>): Array<Record<string, unknown>> => {
  if (block.rows?.length) return block.rows;
  if (block.table?.rows?.length) return block.table.rows;
  const items = seriesItems(block);
  if (items.length) {
    return items.map((item, index) => ({
      id: item.id ?? String(index),
      label: item.label ?? item.name ?? null,
      value: item.value ?? null
    }));
  }
  if (block.jobs?.length) {
    return block.jobs.map((job) => ({
      id: job.id,
      table: job.table ?? null,
      engine: job.engine ?? null,
      status: job.status ?? null,
      started: job.startedAt ?? job.started_at ?? null,
      finished: job.finishedAt ?? job.finished_at ?? null
    }));
  }
  return [];
};

const tableColumns = (block: Extract<AssistantBlock, { type: "chart" }>, rows: Array<Record<string, unknown>>): AssistantColumn[] => {
  if (block.columns?.length) return block.columns;
  if (block.table?.columns?.length) return block.table.columns;
  const sample = rows[0];
  if (!sample) return [];
  return Object.keys(sample)
    .filter((key) => key !== "id")
    .map((key) => ({ key, header: key }));
};

export const ChartBlock = ({ block }: { block: Extract<AssistantBlock, { type: "chart" }> }) => {
  const [view, setView] = useState<"chart" | "table">("chart");
  const rows = useMemo(() => tableRows(block), [block]);
  const columns = useMemo(() => tableColumns(block, rows), [block, rows]);
  const kit = block.kind === "ranked_bars" || block.kind === "run_lanes";

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-medium text-foreground">{block.title ?? "Chart"}</h3>
        <div role="group" aria-label="Chart or table" className="inline-flex gap-1">
          <Button type="button" size="sm" variant={view === "chart" ? "secondary" : "ghost"} aria-pressed={view === "chart"} onClick={() => setView("chart")}>
            Chart
          </Button>
          <Button type="button" size="sm" variant={view === "table" ? "secondary" : "ghost"} aria-pressed={view === "table"} onClick={() => setView("table")}>
            Table
          </Button>
        </div>
      </div>
      {view === "chart" && block.kind === "ranked_bars" ? (
        <RankedBars
          title={block.title ?? "Ranked"}
          unit={block.unit ?? ""}
          items={seriesItems(block).map((item, index) => ({
            id: item.id ?? String(index),
            label: item.label ?? item.name ?? "-",
            value: chartValue(item.value)
          }))}
        />
      ) : null}
      {view === "chart" && block.kind === "run_lanes" ? (
        <RunLanes
          jobs={(block.jobs?.length ? block.jobs : (block.series ?? [])).map((job, index) => {
            const row = job as Record<string, unknown>;
            const started = row.startedAt ?? row.started_at;
            const finished = row.finishedAt ?? row.finished_at;
            return {
              id: typeof row.id === "string" ? row.id : String(index),
              table: typeof row.table === "string" ? row.table : "-",
              engine: typeof row.engine === "string" ? row.engine : "-",
              status: typeof row.status === "string" ? row.status : "-",
              startedAt: typeof started === "string" ? started : null,
              finishedAt: typeof finished === "string" ? finished : null
            };
          })}
        />
      ) : null}
      {view === "table" || !kit ? (
        <div className="space-y-1">
          {view === "chart" && !kit ? (
            <p className="text-xs text-muted-foreground">Shown as a table.</p>
          ) : null}
          <AssistantBlockTable columns={columns} rows={rows} />
        </div>
      ) : null}
      <EvidenceLinks ids={block.evidence_ids} />
    </div>
  );
};
