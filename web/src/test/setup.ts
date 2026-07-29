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
