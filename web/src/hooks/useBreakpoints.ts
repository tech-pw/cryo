import { Grid } from "antd";
import { useMemo } from "react";

export interface Breakpoints {
  isMobile: boolean;
  isTablet: boolean;
  isDesktop: boolean;
  isWide: boolean;
}

/**
 * Ant's `useBreakpoint` returns the largest matched breakpoint; smaller
 * booleans are not always set. We compute desktop/wide from the largest
 * present key so callers can use a stable contract.
 */
export const useBreakpoints = (): Breakpoints => {
  const bp = Grid.useBreakpoint();
  return useMemo(() => {
    const isWide = !!bp.lg || !!bp.xl || !!bp.xxl;
    const isDesktop = !!bp.md || isWide;
    const isTablet = !!bp.sm && !isDesktop;
    const isMobile = !!bp.xs && !isTablet && !isDesktop;
    return { isMobile, isTablet, isDesktop, isWide };
  }, [bp.xs, bp.sm, bp.md, bp.lg, bp.xl, bp.xxl]);
};