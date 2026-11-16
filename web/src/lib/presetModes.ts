import type { Mode, PresetModeCatalog, PresetModeInfo, PresetParamDefault } from "../api/types";
import { formatBytesGb } from "./format";

/** Display names; what each mode runs comes from GET /presets/modes (cryo/planner/guardrails.py). */
export const MODE_LABELS: Record<Mode, string> = {
  light: "Light",
  standard: "Standard",
  deep: "Deep",
  manifest_only: "Manifest only"
};

export const modeLabel = (mode: string): string => MODE_LABELS[mode as Mode] ?? mode;

export const operationLabel = (op: string): string => op.replaceAll("_", " ");

export const findMode = (
  catalog: PresetModeCatalog | undefined,
  mode: string | undefined
): PresetModeInfo | undefined => catalog?.modes.find((item) => item.mode === mode);

export const modeOperations = (
  catalog: PresetModeCatalog | undefined,
  mode: string | undefined
): string[] | null => {
  const info = findMode(catalog, mode);
  return info ? [...info.operations] : null;
};

export interface OperationDiff {
  added: string[];
  removed: string[];
}

/** Operations gained and lost when a preset moves from one mode to another, in run order. */
export const modeOperationDiff = (
  catalog: PresetModeCatalog | undefined,
  from: string,
  to: string
): OperationDiff | null => {
  const before = modeOperations(catalog, from);
  const after = modeOperations(catalog, to);
  if (!before || !after) return null;
  return {
    added: after.filter((op) => !before.includes(op)),
    removed: before.filter((op) => !after.includes(op))
  };
};

export type DeepParamKey = "orphan_cadence_days" | "orphan_catchup_min_bytes" | "orphan_max_listing_keys" | "min_manifests";

export interface EffectiveParam {
  value: number | null;
  source: "preset" | "deployment" | "built_in" | "unknown";
}

/** The value a run uses for a deep-mode param: the preset's own, else the server default. */
export const effectiveDeepParam = (
  catalog: PresetModeCatalog | undefined,
  key: DeepParamKey,
  presetValue: number | null | undefined
): EffectiveParam => {
  if (presetValue != null && Number.isFinite(presetValue)) return { value: presetValue, source: "preset" };
  const fallback: PresetParamDefault | undefined = catalog?.param_defaults.find((item) => item.key === key);
  if (!fallback) return { value: null, source: "unknown" };
  return { value: fallback.value, source: fallback.source };
};

export const SOURCE_LABELS: Record<EffectiveParam["source"], string> = {
  preset: "set on this preset",
  deployment: "deployment default",
  built_in: "built-in default",
  unknown: "default"
};

export const formatOrphanBacklog = (bytes: number | null | undefined, tables: number | undefined): string | null => {
  if (bytes == null) return null;
  if (bytes === 0) return "No orphan files found by the last storage listing.";
  const count = tables ?? 0;
  return `${formatBytesGb(bytes)} of orphan files across ${count} ${count === 1 ? "table" : "tables"} (last storage listing).`;
};
