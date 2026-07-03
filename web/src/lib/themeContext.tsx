import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import {
  cryoColorsLight,
  cryoColorsDark,
  getChartAxisTick,
  getChartTooltipProps,
  type CryoColors
} from "./theme";

export type Theme = "light" | "dark";

interface ThemeContextValue {
  theme: Theme;
  setTheme: (theme: Theme) => void;
  toggleTheme: () => void;
  colors: CryoColors;
  chartAxisTick: ReturnType<typeof getChartAxisTick>;
  chartTooltipProps: ReturnType<typeof getChartTooltipProps>;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

const STORAGE_KEY = "cryo-theme";

const getSystemPreference = (): Theme =>
  window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";

const getStoredTheme = (): Theme | null => {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    // localStorage not available
  }
  return null;
};

export const ThemeProvider = ({ children }: { children: React.ReactNode }) => {
  const [theme, setThemeState] = useState<Theme>(() => getStoredTheme() ?? getSystemPreference());

  const setTheme = useCallback((newTheme: Theme) => {
    setThemeState(newTheme);
    try {
      localStorage.setItem(STORAGE_KEY, newTheme);
    } catch {
      // localStorage not available
    }
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme(theme === "light" ? "dark" : "light");
  }, [theme, setTheme]);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  const colors = useMemo(() => (theme === "dark" ? cryoColorsDark : cryoColorsLight), [theme]);
  const chartAxisTick = useMemo(() => getChartAxisTick(colors), [colors]);
  const chartTooltipProps = useMemo(() => getChartTooltipProps(colors), [colors]);

  return (
    <ThemeContext.Provider
      value={{ theme, setTheme, toggleTheme, colors, chartAxisTick, chartTooltipProps }}
    >
      {children}
    </ThemeContext.Provider>
  );
};

export const useTheme = (): ThemeContextValue => {
  const context = useContext(ThemeContext);
  if (!context) throw new Error("useTheme must be used within ThemeProvider");
  return context;
};
