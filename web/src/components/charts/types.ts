export type ChartProvenance = "measured" | "modelled" | "modeled" | "estimated-pricing" | "partial";

export type ChartCardState = "ready" | "empty" | "loading" | "error" | "partial" | "stale";

export type PlotCell = string | number | boolean | null | undefined;

export interface PlottedRow {
  id?: string;
  [column: string]: PlotCell;
}

export interface PlottedColumn {
  key: string;
  header: string;
  align?: "left" | "right";
}

export interface ChartSubtitle {
  unit?: string;
  period?: string;
  asOf?: string;
  timeZone?: string;
}

export interface CoverageInput {
  measured: number;
  total: number;
  noun?: string;
}

export interface ChangeAnswerInput {
  label: string;
  previous: number | null;
  current: number | null;
  since: string;
}

export interface ChartCap {
  shown: number;
  total: number;
}
