import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  animateOnce,
  MOTION_EASING,
  MOTION_MS,
  motionMs,
  motionVar,
  REDUCED_MOTION_QUERY,
  useAnimateOnce,
  useReducedMotion
} from "./motion";

const here = dirname(fileURLToPath(import.meta.url));
const tokensCss = readFileSync(resolve(here, "../styles/tokens.css"), "utf8");
const kitCss = readFileSync(resolve(here, "../components/charts/motion-tokens.css"), "utf8");

function installMatchMedia(matches: boolean) {
  const listeners = new Set<() => void>();
  const media = {
    matches,
    media: REDUCED_MOTION_QUERY,
    onchange: null,
    addEventListener: (_type: string, cb: () => void) => {
      listeners.add(cb);
    },
    removeEventListener: (_type: string, cb: () => void) => {
      listeners.delete(cb);
    },
    addListener: (cb: () => void) => {
      listeners.add(cb);
    },
    removeListener: (cb: () => void) => {
      listeners.delete(cb);
    },
    dispatchEvent: () => false,
    emit(next: boolean) {
      media.matches = next;
      listeners.forEach((listener) => listener());
    }
  };
  vi.stubGlobal("matchMedia", (query: string) => {
    if (query === REDUCED_MOTION_QUERY) return media;
    return {
      matches: false,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false
    };
  });
  return media;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("animateOnce", () => {
  it("animates only on the first render without reduced motion", () => {
    expect(animateOnce(false, true)).toBe(true);
    expect(animateOnce(false, false)).toBe(false);
    expect(animateOnce(true, true)).toBe(false);
    expect(animateOnce(true, false)).toBe(false);
  });
});

describe("useReducedMotion", () => {
  it("starts from matchMedia and updates when the query changes", () => {
    const media = installMatchMedia(false);
    const { result } = renderHook(() => useReducedMotion());
    expect(result.current).toBe(false);
    act(() => {
      media.emit(true);
    });
    expect(result.current).toBe(true);
    act(() => {
      media.emit(false);
    });
    expect(result.current).toBe(false);
  });

  it("starts true when reduced motion is already set", () => {
    installMatchMedia(true);
    const { result } = renderHook(() => useReducedMotion());
    expect(result.current).toBe(true);
  });
});

describe("useAnimateOnce", () => {
  it("skips animation after the first render", () => {
    installMatchMedia(false);
    const { result, rerender } = renderHook(() => useAnimateOnce());
    expect(result.current).toBe(true);
    rerender();
    expect(result.current).toBe(false);
  });

  it("skips animation when reduced motion is set", () => {
    installMatchMedia(true);
    const { result, rerender } = renderHook(() => useAnimateOnce());
    expect(result.current).toBe(false);
    rerender();
    expect(result.current).toBe(false);
  });
});

describe("motion tokens", () => {
  it("keeps the existing durations and supplies the two missing tokens", () => {
    expect(MOTION_MS.fast).toBe(120);
    expect(MOTION_MS.base).toBe(180);
    expect(MOTION_MS.medium).toBe(220);
    expect(MOTION_MS.slow).toBe(280);
    expect(MOTION_MS.layout).toBe(360);
    expect(MOTION_MS.highlight).toBe(800);
    expect(MOTION_EASING.out).toBe("cubic-bezier(0.16, 1, 0.3, 1)");
    expect(motionVar("layout")).toBe("var(--motion-layout, 360ms)");
    expect(motionVar("highlight")).toBe("var(--motion-highlight, 800ms)");
    expect(motionMs("slow", false)).toBe(280);
    expect(motionMs("highlight", true)).toBe(0);

    expect(tokensCss).toMatch(/--motion-fast:\s*120ms/);
    expect(tokensCss).toMatch(/--motion-base:\s*180ms/);
    expect(tokensCss).toMatch(/--motion-medium:\s*220ms/);
    expect(tokensCss).toMatch(/--motion-slow:\s*280ms/);
    expect(tokensCss).toContain("--ease-out: cubic-bezier(0.16, 1, 0.3, 1)");
    expect(tokensCss).toContain("--ease-in: cubic-bezier(0.7, 0, 0.84, 0)");
    expect(tokensCss).toContain("--ease-standard: cubic-bezier(0.4, 0, 0.2, 1)");

    expect(kitCss).toMatch(/--motion-layout:\s*360ms/);
    expect(kitCss).toMatch(/--motion-highlight:\s*800ms/);
    expect(kitCss).toContain("prefers-reduced-motion");
    for (const name of ["--motion-layout", "--motion-highlight"] as const) {
      const match = tokensCss.match(new RegExp(`${name}:\\s*(\\d+)ms`));
      if (match) expect(Number(match[1])).toBe(name === "--motion-layout" ? 360 : 800);
    }
  });
});
