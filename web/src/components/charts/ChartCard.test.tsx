import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import chartCardSource from "./ChartCard.tsx?raw";
import { ChartCard, useChartMotion } from "./ChartCard";
import { UNKNOWN_MARK } from "./format";
import type { PlottedRow } from "./types";

const rows: PlottedRow[] = [{ id: "1", files: 4 }];

function Probe() {
  const motion = useChartMotion();
  return (
    <div data-testid="probe" data-active={String(motion.isAnimationActive)} data-duration={String(motion.durationMs)} />
  );
}

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

describe("ChartCard", () => {
  it("does not render a chart without a table", () => {
    render(
      <ChartCard title="How many small files?" table={undefined as unknown as PlottedRow[]}>
        <div>plot-child</div>
      </ChartCard>
    );
    expect(screen.queryByTestId("chart-card")).not.toBeInTheDocument();
    expect(screen.queryByTestId("chart-plot")).not.toBeInTheDocument();
    expect(screen.queryByText("plot-child")).not.toBeInTheDocument();
  });

  it("shows the question, computed answer, subtitle, coverage, cap, and provenance", () => {
    render(
      <ChartCard
        title="How many small files?"
        answer={{ label: "Small files", previous: 100, current: 82, since: "1 Sep" }}
        subtitle={{ unit: "files", period: "since 1 Sep", asOf: "28 Sep", timeZone: "Asia/Kolkata" }}
        coverage={{ measured: 8, total: 12, noun: "actions" }}
        cap={{ shown: 15, total: 40 }}
        provenance="measured"
        table={rows}
      >
        <div>plot-child</div>
      </ChartCard>
    );
    expect(screen.getByRole("heading", { name: "How many small files?" })).toBeInTheDocument();
    expect(screen.getByTestId("chart-answer")).toHaveTextContent("Small files down 18% since 1 Sep");
    expect(screen.getByTestId("chart-subtitle")).toHaveTextContent(
      "files \u00b7 since 1 Sep \u00b7 as of 28 Sep \u00b7 Asia/Kolkata"
    );
    expect(screen.getByTestId("chart-coverage")).toHaveTextContent("Partial \u00b7 measured for 8 of 12 actions");
    expect(screen.getByTestId("chart-cap")).toHaveTextContent("Top 15 of 40");
    expect(screen.getByTestId("chart-provenance")).toHaveTextContent("Measured");
    expect(screen.getByTestId("chart-plot")).toHaveTextContent("plot-child");
    expect(screen.getByText("View data as table")).toBeInTheDocument();
  });

  it("omits the answer line when the change cannot be computed", () => {
    render(
      <ChartCard
        title="How many small files?"
        answer={{ label: "Small files", previous: null, current: 82, since: "1 Sep" }}
        table={rows}
      />
    );
    expect(screen.queryByTestId("chart-answer")).not.toBeInTheDocument();
  });

  it.each([
    ["modelled", "Modelled"],
    ["estimated-pricing", "Estimated pricing"],
    ["partial", "Partial"]
  ] as const)("shows the %s provenance chip", (provenance, label) => {
    render(<ChartCard title="How many small files?" table={rows} provenance={provenance} />);
    expect(screen.getByTestId("chart-provenance")).toHaveTextContent(label);
  });

  it("renders empty, loading, error with retry, partial, and stale", () => {
    const { rerender } = render(
      <ChartCard title="How many small files?" table={[]} state="empty">
        <div>plot-child</div>
      </ChartCard>
    );
    expect(screen.getByTestId("chart-card")).toHaveAttribute("data-state", "empty");
    expect(screen.getByText("Nothing measured yet")).toBeInTheDocument();
    expect(screen.getByText(/Run a scan to plot this/)).toBeInTheDocument();
    expect(screen.queryByTestId("chart-plot")).not.toBeInTheDocument();
    expect(screen.queryByText("plot-child")).not.toBeInTheDocument();

    rerender(
      <ChartCard title="How many small files?" table={rows} state="loading">
        <div>plot-child</div>
      </ChartCard>
    );
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
    expect(screen.queryByTestId("chart-plot")).not.toBeInTheDocument();

    const onRetry = vi.fn();
    rerender(
      <ChartCard title="How many small files?" table={rows} state="error" onRetry={onRetry} errorMessage="Scan failed">
        <div>plot-child</div>
      </ChartCard>
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Scan failed");
    expect(screen.queryByTestId("chart-plot")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledOnce();

    rerender(
      <ChartCard title="How many small files?" table={rows} state="partial">
        <div>plot-child</div>
      </ChartCard>
    );
    expect(screen.getByTestId("chart-card")).toHaveAttribute("data-state", "partial");
    expect(screen.getByTestId("chart-partial")).toHaveTextContent("Partial");
    expect(screen.getByTestId("chart-plot")).toBeInTheDocument();

    rerender(
      <ChartCard title="How many small files?" table={rows} state="stale" staleMessage="Stale">
        <div>plot-child</div>
      </ChartCard>
    );
    expect(screen.getByTestId("chart-stale")).toHaveTextContent("Stale");
    expect(screen.getByTestId("chart-plot")).toHaveTextContent("plot-child");
  });

  it("keeps unknowns in the table as gaps, never zero", () => {
    render(
      <ChartCard
        title="Which partitions were measured?"
        table={[
          { id: "partition-a", files: null },
          { id: "partition-b", files: 0 }
        ]}
        columns={[
          { key: "id", header: "Partition" },
          { key: "files", header: "Files", align: "right" }
        ]}
      />
    );
    const unknownRow = screen.getByText("partition-a").closest("tr");
    const zeroRow = screen.getByText("partition-b").closest("tr");
    expect(unknownRow).toHaveTextContent(UNKNOWN_MARK);
    expect(unknownRow).not.toHaveTextContent("0");
    expect(screen.getByTitle("Not measured")).toBeInTheDocument();
    expect(zeroRow).toHaveTextContent("0");
    expect(screen.getByText("Not measured")).toBeInTheDocument();
  });

  it("animates on the first render only", () => {
    function Harness() {
      const [tick, setTick] = useState(0);
      return (
        <>
          <button type="button" onClick={() => setTick((value) => value + 1)}>
            Refresh
          </button>
          <ChartCard title="How many small files?" table={rows}>
            <Probe />
            <span>{tick}</span>
          </ChartCard>
        </>
      );
    }
    render(<Harness />);
    expect(screen.getByTestId("chart-card")).toHaveAttribute("data-animation-active", "true");
    expect(screen.getByTestId("chart-card")).toHaveAttribute("data-animation-duration", "280");
    expect(screen.getByTestId("probe")).toHaveAttribute("data-active", "true");
    expect(screen.getByTestId("probe")).toHaveAttribute("data-duration", "280");
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(screen.getByTestId("chart-card")).toHaveAttribute("data-animation-active", "false");
    expect(screen.getByTestId("chart-card")).toHaveAttribute("data-animation-duration", "0");
    expect(screen.getByTestId("probe")).toHaveAttribute("data-active", "false");
  });

  it("skips animation when reduced motion is set", () => {
    installMatchMedia(true);
    render(
      <ChartCard title="How many small files?" table={rows}>
        <Probe />
      </ChartCard>
    );
    expect(screen.getByTestId("chart-card")).toHaveAttribute("data-animation-active", "false");
    expect(screen.getByTestId("probe")).toHaveAttribute("data-duration", "0");
  });

  it("does not accept a free-text answer prop", () => {
    expect(chartCardSource).not.toMatch(/answer\?:\s*string/);
    expect(chartCardSource).toMatch(/formatChangeAnswer/);
    expect(chartCardSource).toMatch(/table:\s*readonly PlottedRow\[\]/);
    expect(chartCardSource).not.toMatch(/table\?:/);
  });
});
