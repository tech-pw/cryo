import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    Grid: { useBreakpoint: vi.fn() }
  };
});

import { Grid } from "antd";
import { useBreakpoints } from "./useBreakpoints";

const mocked = Grid.useBreakpoint as unknown as ReturnType<typeof vi.fn>;

describe("useBreakpoints", () => {
  beforeEach(() => mocked.mockReset());
  afterEach(() => vi.restoreAllMocks());

  it("treats xs as mobile", () => {
    mocked.mockReturnValue({ xs: true });
    const { result } = renderHook(() => useBreakpoints());
    expect(result.current.isMobile).toBe(true);
    expect(result.current.isDesktop).toBe(false);
  });

  it("treats md as desktop", () => {
    mocked.mockReturnValue({ md: true });
    const { result } = renderHook(() => useBreakpoints());
    expect(result.current.isDesktop).toBe(true);
    expect(result.current.isMobile).toBe(false);
    expect(result.current.isWide).toBe(false);
  });

  it("treats lg+ as wide", () => {
    mocked.mockReturnValue({ lg: true, xl: true });
    const { result } = renderHook(() => useBreakpoints());
    expect(result.current.isWide).toBe(true);
    expect(result.current.isDesktop).toBe(true);
  });
});