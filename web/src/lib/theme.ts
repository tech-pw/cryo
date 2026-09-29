const THEME_STORAGE_KEY = "cryo-theme";

export const cryoColorsLight = {
  bg: "#ffffff",
  layoutBg: "#f5fafd",
  surfaceSunken: "#f6f8fb",
  surface: "#ffffff",
  panel: "#f3f5f8",
  panelStrong: "#eef1f6",
  ink: "#131b24",
  inkSoft: "#545f6d",
  inkFaint: "#8b95a8",
  muted: "#5c667a",
  mutedSubtle: "#8b95a8",
  border: "#e1e6ed",
  borderStrong: "#c7d0dc",
  primary: "#277abe",
  primaryHover: "#266fa8",
  primarySoft: "#eaf7fd",
  tableHeader: "#f6f8fb",
  chartGrid: "#e8ecf2",
  chartPrimary: "#277abe",
  gradeA: "#1e8e5a",
  gradeB: "#4d7c0f",
  gradeC: "#a9791e",
  gradeD: "#c1631b",
  gradeF: "#b23a2e",
  costCurrent: "#277abe",
  costProjected: "#0f9b8e",
  savings: "#1e8e5a"
} as const;

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
  primary: "#68c1ea",
  primaryHover: "#90d4f0",
  primarySoft: "#0f2a40",
  tableHeader: "#0e141f",
  chartGrid: "#202a38",
  chartPrimary: "#68c1ea",
  gradeA: "#3fbe85",
  gradeB: "#8cc152",
  gradeC: "#e0b03d",
  gradeD: "#f0904f",
  gradeF: "#f0665a",
  costCurrent: "#68c1ea",
  costProjected: "#35c7b8",
  savings: "#3fbe85"
} as const;

const sidebarLight = {
  bg: "#f5fafd",
  text: "#5c667a",
  textActive: "#1f5f94",
  accent: "#277abe",
  hoverBg: "#eef1f6",
  activeBg: "#eaf7fd"
} as const;

const sidebarDark = {
  bg: "#0e141f",
  text: "#a6b2c0",
  textActive: "#90d4f0",
  accent: "#68c1ea",
  hoverBg: "#141c28",
  activeBg: "#0f2a40"
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

const CSS_VAR_BY_KEY: Record<keyof CryoColors, string> = {
  bg: "--color-bg",
  layoutBg: "--color-layout-bg",
  surfaceSunken: "--color-surface-sunken",
  surface: "--primitive-surface",
  panel: "--color-panel",
  panelStrong: "--color-panel-strong",
  ink: "--color-ink",
  inkSoft: "--color-ink-soft",
  inkFaint: "--color-ink-faint",
  muted: "--color-muted",
  mutedSubtle: "--color-muted-subtle",
  border: "--color-border-default",
  borderStrong: "--color-border-strong",
  primary: "--color-primary",
  primaryHover: "--color-primary-hover",
  primarySoft: "--color-primary-soft",
  tableHeader: "--color-table-header",
  chartGrid: "--color-chart-grid",
  chartPrimary: "--color-chart-primary",
  gradeA: "--grade-a",
  gradeB: "--grade-b",
  gradeC: "--grade-c",
  gradeD: "--grade-d",
  gradeF: "--grade-f",
  costCurrent: "--color-cost-current",
  costProjected: "--color-cost-projected",
  savings: "--color-savings"
};

export type ChartTheme = "light" | "dark";

export const resolveChartTheme = (): ChartTheme => {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    // localStorage unavailable
  }
  if (typeof document !== "undefined") {
    const attr = document.documentElement.getAttribute("data-theme");
    if (attr === "light" || attr === "dark") return attr;
  }
  if (typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches) {
    return "dark";
  }
  return "light";
};

const isCssColor = (value: string): boolean => /^#|^rgb|^hsl/i.test(value) && !value.includes("var(");

const readCssColor = (variable: string): string | null => {
  if (typeof document === "undefined") return null;
  const host = document.body ?? document.documentElement;
  const probe = document.createElement("span");
  const control = document.createElement("span");
  try {
    probe.style.color = `var(${variable})`;
    control.style.color = "var(--cryo-missing-token)";
    host.append(probe, control);
    const resolved = getComputedStyle(probe).color.trim();
    const missing = getComputedStyle(control).color.trim();
    if (!resolved || resolved === missing || !isCssColor(resolved)) return null;
    return resolved;
  } catch {
    return null;
  } finally {
    probe.remove();
    control.remove();
  }
};

const paletteFor = (theme: ChartTheme): CryoColors => (theme === "dark" ? cryoColorsDark : cryoColorsLight);

const loadColors = (theme: ChartTheme): CryoColors => {
  const fallback = paletteFor(theme);
  if (typeof document === "undefined") return fallback;
  const attr = document.documentElement.getAttribute("data-theme");
  if (attr !== theme) return fallback;
  const next: CryoColors = { ...fallback };
  for (const key of Object.keys(CSS_VAR_BY_KEY) as (keyof CryoColors)[]) {
    const live = readCssColor(CSS_VAR_BY_KEY[key]);
    if (live) next[key] = live;
  }
  return next;
};

let colorCache: { key: string; colors: CryoColors } | null = null;

export const readActiveColors = (): CryoColors => {
  const theme = resolveChartTheme();
  const attr = typeof document === "undefined" ? "" : (document.documentElement.getAttribute("data-theme") ?? "");
  const key = `${theme}|${attr}`;
  if (colorCache?.key === key) return colorCache.colors;
  const colors = loadColors(theme);
  colorCache = { key, colors };
  return colors;
};

export const cryoColors: CryoColors = new Proxy(cryoColorsLight, {
  get(target, prop, receiver) {
    if (typeof prop === "string" && prop in target) {
      return readActiveColors()[prop as keyof CryoColors];
    }
    return Reflect.get(target, prop, receiver);
  }
});

export const sidebarColors = new Proxy(sidebarLight, {
  get(target, prop, receiver) {
    const palette = resolveChartTheme() === "dark" ? sidebarDark : sidebarLight;
    if (typeof prop === "string" && prop in palette) {
      return palette[prop as keyof typeof palette];
    }
    return Reflect.get(target, prop, receiver);
  }
});

const operationColorBase: Record<string, string> = {
  optimize: "#277abe",
  expire_snapshots: "#2f9e6b",
  remove_orphan_files: "#c9a227",
  optimize_manifests: "#8b5cf6"
};

export const operationColors: Record<string, string> = new Proxy(operationColorBase, {
  get(target, prop, receiver) {
    if (prop === "optimize") return readActiveColors().chartPrimary;
    return Reflect.get(target, prop, receiver);
  }
});

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
  cryoColorsLight.gradeA,
  cryoColorsLight.gradeB,
  cryoColorsLight.gradeC,
  cryoColorsLight.gradeD,
  cryoColorsLight.gradeF
] as const;

export const chartAxisTick = {
  get fill() {
    return readActiveColors().muted;
  },
  fontSize: 11
};

export const chartTooltipProps = {
  get contentStyle() {
    const colors = readActiveColors();
    return {
      background: colors.bg,
      border: `1px solid ${colors.border}`,
      borderRadius: 6,
      color: colors.ink,
      boxShadow: "0 4px 12px rgb(28 36 51 / 10%)"
    };
  },
  get itemStyle() {
    return { color: readActiveColors().muted };
  },
  get labelStyle() {
    return { color: readActiveColors().ink, fontWeight: 600 as const };
  }
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
