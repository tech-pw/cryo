/** Hex palette for Ant Design tokens - Light theme */
export const cryoColorsLight = {
  bg: "#ffffff",
  layoutBg: "#f3f5f8",
  surfaceSunken: "#f6f8fb",
  surface: "#fafafa",
  panel: "#f3f5f8",
  panelStrong: "#eef1f6",
  ink: "#131b24",
  inkSoft: "#545f6d",
  inkFaint: "#8791a0",
  muted: "#5c667a",
  mutedSubtle: "#8b95a8",
  border: "#e1e6ed",
  borderStrong: "#c7d0dc",
  primary: "#3d6fd4",
  primaryHover: "#3566c8",
  primarySoft: "#e8effc",
  tableHeader: "#f6f8fb",
  chartGrid: "#e8ecf2",
  chartPrimary: "#3d6fd4",
  gradeA: "#1e8e5a",
  gradeB: "#3d6fd4",
  gradeC: "#a9791e",
  gradeD: "#c1631b",
  gradeF: "#b23a2e",
  costCurrent: "#3d6fd4",
  costProjected: "#0f9b8e",
  savings: "#1e8e5a"
} as const;

/** Hex palette for Ant Design tokens - Dark theme */
export const cryoColorsDark = {
  bg: "#0b0f16",
  layoutBg: "#0b0f16",
  surfaceSunken: "#0e141f",
  surface: "#0e141f",
  panel: "#0e141f",
  panelStrong: "#141c28",
  ink: "#e7ecf3",
  inkSoft: "#a6b2c0",
  inkFaint: "#6e7a8a",
  muted: "#a6b2c0",
  mutedSubtle: "#6e7a8a",
  border: "#202a38",
  borderStrong: "#2e3b4c",
  primary: "#5b9ce8",
  primaryHover: "#6faaf0",
  primarySoft: "#16283f",
  tableHeader: "#0e141f",
  chartGrid: "#202a38",
  chartPrimary: "#5b9ce8",
  gradeA: "#3fbe85",
  gradeB: "#5b9ce8",
  gradeC: "#e0b03d",
  gradeD: "#f0904f",
  gradeF: "#f0665a",
  costCurrent: "#5b9ce8",
  costProjected: "#35c7b8",
  savings: "#3fbe85"
} as const;

/** Always-dark sidebar colors */
export const sidebarColors = {
  bg: "#0c121b",
  text: "#c7d2de",
  textActive: "#ffffff",
  accent: "#3d6fd4",
  hoverBg: "#141c28"
} as const;

export type CryoColors = {
  bg: string;
  layoutBg: string;
  surfaceSunken: string;
  surface: string;
  panel: string;
  panelStrong: string;
  ink: string;
  inkSoft: string;
  inkFaint: string;
  muted: string;
  mutedSubtle: string;
  border: string;
  borderStrong: string;
  primary: string;
  primaryHover: string;
  primarySoft: string;
  tableHeader: string;
  chartGrid: string;
  chartPrimary: string;
  gradeA: string;
  gradeB: string;
  gradeC: string;
  gradeD: string;
  gradeF: string;
  costCurrent: string;
  costProjected: string;
  savings: string;
};

/** Default to light theme for backwards compatibility */
export const cryoColors = cryoColorsLight;

export const operationColors: Record<string, string> = {
  optimize: "#3d6fd4",
  expire_snapshots: "#2f9e6b",
  remove_orphan_files: "#c9a227",
  optimize_manifests: "#8b5cf6"
};

export const operationLabels: Record<string, string> = {
  optimize: "Compaction",
  expire_snapshots: "Snapshot expiry",
  remove_orphan_files: "Orphan removal",
  optimize_manifests: "Manifest rewrite"
};

export const semanticStatusColor = (
  status: "good" | "warn" | "bad" | "neutral",
  palette: CryoColors = cryoColors
): string => {
  if (status === "good") return palette.gradeA;
  if (status === "warn") return palette.gradeC;
  if (status === "bad") return palette.gradeF;
  return palette.muted;
};

export const statusFromThreshold = (
  value: number,
  thresholds: { good: number; warn: number }
): "good" | "warn" | "bad" => {
  if (value >= thresholds.good) return "good";
  if (value >= thresholds.warn) return "warn";
  return "bad";
};

export const sharedPieColors = [
  cryoColors.gradeA,
  cryoColors.gradeB,
  cryoColors.gradeC,
  cryoColors.gradeD,
  cryoColors.gradeF
] as const;

export const chartAxisTick = { fill: cryoColors.muted, fontSize: 11 };

export const chartTooltipProps = {
  contentStyle: {
    background: cryoColors.bg,
    border: `1px solid ${cryoColors.border}`,
    borderRadius: 6,
    color: cryoColors.ink,
    boxShadow: "0 4px 12px rgb(28 36 51 / 10%)"
  },
  itemStyle: { color: cryoColors.muted },
  labelStyle: { color: cryoColors.ink, fontWeight: 600 }
};

export const getChartAxisTick = (colors: CryoColors) => ({ fill: colors.muted, fontSize: 11 });

export const getChartTooltipProps = (colors: CryoColors) => ({
  contentStyle: {
    background: colors.bg,
    border: `1px solid ${colors.border}`,
    borderRadius: 6,
    color: colors.ink,
    boxShadow: "0 4px 12px rgb(28 36 51 / 10%)"
  },
  itemStyle: { color: colors.muted },
  labelStyle: { color: colors.ink, fontWeight: 600 }
});
