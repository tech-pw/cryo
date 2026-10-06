import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { computeBreakpoints, useBreakpoints } from "./useBreakpoints";

describe("computeBreakpoints", () => {
  it("treats xs as mobile", () => {
    expect(computeBreakpoints({ xs: true })).toEqual({
      isMobile: true,
      isTablet: false,
      isDesktop: false,
      isWide: false
    });
  });

  it("treats md as desktop", () => {
    expect(computeBreakpoints({ md: true })).toEqual({
      isMobile: false,
      isTablet: false,
      isDesktop: true,
      isWide: false
    });
  });

  it("treats lg+ as wide", () => {
    expect(computeBreakpoints({ lg: true, xl: true })).toEqual({
      isMobile: false,
      isTablet: false,
      isDesktop: true,
      isWide: true
    });
  });
});

describe("useBreakpoints", () => {
  it("returns a stable breakpoint object", () => {
    const { result } = renderHook(() => useBreakpoints());
    expect(result.current).toEqual(
      expect.objectContaining({
        isMobile: expect.any(Boolean),
        isTablet: expect.any(Boolean),
        isDesktop: expect.any(Boolean),
        isWide: expect.any(Boolean)
      })
    );
  });
});
