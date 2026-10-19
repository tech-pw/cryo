import { cleanup, render as rtlRender } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { createElement, type ReactElement } from "react";
import { afterEach, vi } from "vitest";
import { CryoTooltipProvider } from "../components/ui/tooltip";

vi.mock("@testing-library/react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@testing-library/react")>();
  return {
    ...actual,
    render: (ui: ReactElement, options?: Parameters<typeof rtlRender>[1]) =>
      actual.render(createElement(CryoTooltipProvider, null, ui), options)
  };
});

class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}

Object.defineProperty(globalThis, "ResizeObserver", {
  writable: true,
  configurable: true,
  value: ResizeObserverMock
});

Object.defineProperty(globalThis, "matchMedia", {
  writable: true,
  configurable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false
  })
});

afterEach(() => cleanup());

// jsdom tears its window down when the worker finishes a file, and a timer or
// promise scheduled during a test can resolve a tick later and touch
// `document` after that. Vitest reports it as an unhandled error attributed to
// whichever file was running, which fails an otherwise green run (seen in CI
// as "ReferenceError: document is not defined" from OverviewPanel.test.tsx
// with all 845 tests passing). Once the document is gone, later reads are
// misses rather than crashes.
afterEach(() => {
  if (typeof document !== "undefined") return;
  vi.stubGlobal("document", {
    documentElement: { getAttribute: () => null, setAttribute: () => {}, style: {} },
    body: null,
    querySelector: () => null,
    querySelectorAll: () => [],
    getElementById: () => null,
    createElement: () => ({ style: {}, setAttribute: () => {}, appendChild: () => {} }),
    addEventListener: () => {},
    removeEventListener: () => {}
  });
});
