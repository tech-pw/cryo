import { useEffect, useMemo, useState } from "react";

export interface Breakpoints {
  isMobile: boolean;
  isTablet: boolean;
  isDesktop: boolean;
  isWide: boolean;
}

const QUERIES = {
  xs: "(max-width: 575px)",
  sm: "(min-width: 576px) and (max-width: 767px)",
  md: "(min-width: 768px) and (max-width: 991px)",
  lg: "(min-width: 992px) and (max-width: 1199px)",
  xl: "(min-width: 1200px) and (max-width: 1599px)",
  xxl: "(min-width: 1600px)"
} as const;

export const computeBreakpoints = (flags: Partial<Record<keyof typeof QUERIES, boolean>>): Breakpoints => {
  const isWide = !!flags.lg || !!flags.xl || !!flags.xxl;
  const isDesktop = !!flags.md || isWide;
  const isTablet = !!flags.sm && !isDesktop;
  const isMobile = !!flags.xs && !isTablet && !isDesktop;
  return { isMobile, isTablet, isDesktop, isWide };
};

const readFlags = (): Partial<Record<keyof typeof QUERIES, boolean>> => {
  if (typeof window === "undefined" || !window.matchMedia) return {};
  const flags: Partial<Record<keyof typeof QUERIES, boolean>> = {};
  (Object.keys(QUERIES) as Array<keyof typeof QUERIES>).forEach((key) => {
    if (window.matchMedia(QUERIES[key]).matches) flags[key] = true;
  });
  return flags;
};

/**
 * Responsive layout flags derived from CSS media queries.
 * Mirrors the stable contract previously backed by antd Grid.useBreakpoint.
 */
export const useBreakpoints = (): Breakpoints => {
  const [flags, setFlags] = useState(readFlags);

  useEffect(() => {
    const mediaLists = (Object.keys(QUERIES) as Array<keyof typeof QUERIES>).map((key) =>
      window.matchMedia(QUERIES[key])
    );
    const refresh = () => setFlags(readFlags());
    mediaLists.forEach((media) => media.addEventListener("change", refresh));
    refresh();
    return () => mediaLists.forEach((media) => media.removeEventListener("change", refresh));
  }, []);

  return useMemo(() => computeBreakpoints(flags), [flags]);
};
