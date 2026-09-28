import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import source from "./TimeSeries.tsx?raw";
import { TimeSeries } from "./TimeSeries";

function installMatchMedia(matches: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query.includes("prefers-reduced-motion") ? matches : false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false
  }));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("TimeSeries", () => {
  it("does not draw a null point as zero", () => {
    render(
      <TimeSeries
        title="How did the score move?"
        unit="score"
        provenance="measured"
        markers={[{ at: "2026-09-02T00:00:00Z", label: "Optimize" }]}
        points={[
          { at: "2026-09-01T00:00:00Z", value: 80, grade: "B" },
          { at: "2026-09-02T00:00:00Z", value: null, grade: null },
          { at: "2026-09-03T00:00:00Z", value: 72, grade: "C" }
        ]}
      />
    );

    const plotted = screen.getAllByTestId("timeseries-point");
    expect(plotted).toHaveLength(2);
    expect(plotted[0]).toHaveAttribute("data-value", "80");
    expect(plotted[0]).toHaveAttribute("data-at", "2026-09-01T00:00:00Z");
    expect(plotted[0]).toHaveAttribute("fill", "var(--grade-b)");
    expect(plotted[1]).toHaveAttribute("data-value", "72");
    expect(plotted[1]).toHaveAttribute("fill", "var(--grade-c)");
    expect(plotted.some((node) => node.getAttribute("data-at") === "2026-09-02T00:00:00Z")).toBe(false);
    expect(screen.getByTestId("timeseries-plot")).toHaveAttribute("data-plotted-values", "80,72");
    expect(screen.getByTestId("timeseries-plot")).toHaveAttribute("data-connect-nulls", "false");
    expect(screen.getByTestId("timeseries-gap")).toHaveAttribute("data-at", "2026-09-02T00:00:00Z");
    expect(screen.getByTestId("timeseries-gap")).not.toHaveAttribute("data-value");
    expect(screen.getByText("Gaps are not measured.")).toBeInTheDocument();
    expect(screen.getAllByText("Not measured").length).toBeGreaterThan(0);
    expect(plotted.every((node) => node.getAttribute("data-value") !== "0")).toBe(true);
    expect(screen.getByRole("heading", { name: "How did the score move?" })).toBeInTheDocument();
    expect(screen.getByTestId("chart-answer")).toHaveTextContent("score down 10% since 1 Sep");
    expect(screen.getByRole("button", { name: /Maintenance/ })).toHaveTextContent("Optimize");
    expect(screen.getByTestId("timeseries-marker")).toHaveAttribute("data-kind", "maintenance");
    expect(screen.getByText("View data as table")).toBeInTheDocument();
    expect(screen.getByTestId("chart-provenance")).toHaveTextContent("Measured");
  });

  it("shows a provided answer line and colours grades with tokens", () => {
    render(
      <TimeSeries
        title="How did the score move?"
        answer="Score held"
        unit="score"
        points={[{ at: "2026-09-01T00:00:00Z", value: null }, { at: "2026-09-02T00:00:00Z", value: 72, grade: "A" }]}
      />
    );
    expect(screen.getByTestId("chart-answer")).toHaveTextContent("Score held");
    expect(screen.getAllByTestId("timeseries-point")).toHaveLength(1);
    expect(screen.getByTestId("timeseries-point")).toHaveAttribute("fill", "var(--grade-a)");
    expect(screen.getByTestId("timeseries-point")).toHaveAttribute("data-value", "72");
    expect(source).toContain("var(--grade-a)");
    expect(source).toContain("var(--grade-f)");
    expect(source).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  });

  it("does not animate when reduced motion is set", () => {
    installMatchMedia(true);
    render(
      <TimeSeries
        title="How did the score move?"
        unit="score"
        points={[{ at: "2026-09-02T00:00:00Z", value: 72, grade: "A" }]}
      />
    );
    expect(screen.getByTestId("chart-card")).toHaveAttribute("data-animation-active", "false");
    expect(screen.getByTestId("timeseries-plot")).toHaveAttribute("data-animation-duration", "0");
    expect(screen.getByTestId("timeseries-plot")).toHaveAttribute("data-reduced-motion", "true");
    expect(screen.getByTestId("timeseries-plot")).toHaveStyle({ animation: "none", transition: "none" });
  });
});
