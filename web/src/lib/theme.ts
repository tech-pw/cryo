/** Hex palette for Ant Design tokens (AntD cannot derive from oklch seed colors). */
export const cryoColors = {
  bg: "#ffffff",
  surface: "#fafafa",
  panel: "#f3f5f8",
  panelStrong: "#eef1f6",
  ink: "#1c2433",
  muted: "#5c667a",
  mutedSubtle: "#8b95a8",
  border: "#d5dbe6",
  borderStrong: "#b8c2d4",
  primary: "#3d6fd4",
  primaryHover: "#3566c8",
  primarySoft: "#e8effc",
  tableHeader: "#f6f8fb",
  chartGrid: "#e8ecf2",
  chartPrimary: "#3d6fd4",
  gradeA: "#2f9e6b",
  gradeB: "#3d6fd4",
  gradeC: "#c9a227",
  gradeD: "#d97706",
  gradeF: "#c0392b"
} as const;

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
