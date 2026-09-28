import type { ChangeAnswerInput, ChartCap, ChartProvenance, ChartSubtitle, CoverageInput } from "./types";

export const UNKNOWN_MARK = "\u2014";

const PROVENANCE_LABEL: Record<ChartProvenance, string> = {
  measured: "Measured",
  modelled: "Modelled",
  modeled: "Modelled",
  "estimated-pricing": "Estimated pricing",
  partial: "Partial"
};

export type ByteUnit = "B" | "KB" | "MB" | "GB" | "GiB";

const BYTE_DIVISOR: Record<ByteUnit, number> = {
  B: 1,
  KB: 1000,
  MB: 1000 ** 2,
  GB: 1000 ** 3,
  GiB: 1024 ** 3
};

export function provenanceLabel(provenance: ChartProvenance): string {
  return PROVENANCE_LABEL[provenance];
}

export function formatTableCell(value: unknown): string {
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return UNKNOWN_MARK;
    return String(value);
  }
  if (value === null || value === undefined || value === "") return UNKNOWN_MARK;
  return String(value);
}

export function isUnknownPlotValue(value: unknown): boolean {
  return formatTableCell(value) === UNKNOWN_MARK;
}

function formatMagnitude(value: number, unit: string): string {
  if (value !== 0 && Math.abs(value) < 0.05) {
    return value < 0 ? `> -0.1 ${unit}` : `< 0.1 ${unit}`;
  }
  const rendered = Number.isInteger(value) ? String(value) : value.toFixed(1);
  if (rendered === "0.0" || rendered === "-0.0") {
    return value < 0 ? `> -0.1 ${unit}` : value === 0 ? `0 ${unit}` : `< 0.1 ${unit}`;
  }
  return `${rendered} ${unit}`;
}

export function formatFixedUnit(value: number | null | undefined, unit: string): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return UNKNOWN_MARK;
  return formatMagnitude(value, unit);
}

export function formatFixedBytes(bytes: number | null | undefined, unit: ByteUnit): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes)) return UNKNOWN_MARK;
  return formatMagnitude(bytes / BYTE_DIVISOR[unit], unit);
}

export function formatSubtitle(input: ChartSubtitle): string {
  const parts: string[] = [];
  if (input.unit) parts.push(input.unit);
  if (input.period) parts.push(input.period);
  if (input.asOf) parts.push(`as of ${input.asOf}`);
  if (input.timeZone) parts.push(input.timeZone);
  return parts.join(" \u00b7 ");
}

export function formatCoverage(input: CoverageInput): string | null {
  if (!Number.isFinite(input.measured) || !Number.isFinite(input.total) || input.total <= 0) return null;
  if (input.measured >= input.total) return null;
  const noun = input.noun ?? "items";
  return `Partial \u00b7 measured for ${input.measured} of ${input.total} ${noun}`;
}

export function formatCap(input: ChartCap): string | null {
  if (!Number.isFinite(input.shown) || !Number.isFinite(input.total) || input.shown >= input.total) return null;
  return `Top ${input.shown} of ${input.total}`;
}

export function formatChangeAnswer(input: ChangeAnswerInput): string | null {
  if (!input || typeof input !== "object") return null;
  if (typeof input.label !== "string" || !input.label.trim()) return null;
  if (typeof input.since !== "string" || !input.since.trim()) return null;
  const { previous, current, label, since } = input;
  if (previous === null || current === null) return null;
  if (!Number.isFinite(previous) || !Number.isFinite(current) || previous === 0) return null;
  const pct = ((current - previous) / Math.abs(previous)) * 100;
  const rounded = Math.round(Math.abs(pct));
  if (rounded === 0) return `${label} unchanged since ${since}`;
  const direction = current < previous ? "down" : "up";
  return `${label} ${direction} ${rounded}% since ${since}`;
}
