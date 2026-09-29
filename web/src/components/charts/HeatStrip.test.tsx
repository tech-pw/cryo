import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import source from "./HeatStrip.tsx?raw";
import { HEAT_STRIP_WINDOW, HeatStrip, type HeatStripPartition } from "./HeatStrip";

function manyPartitions(count: number, nextKey?: string): HeatStripPartition[] {
  return Array.from({ length: count }, (_, index) => {
    const keyIndex = count - 1 - index;
    return {
      id: `p-${keyIndex}`,
      key: `p-${keyIndex}`,
      smallFiles: keyIndex,
      files: 10,
      queries: keyIndex === 0 ? 0 : 1,
      lastCompactionAt: "2026-09-01T00:00:00Z",
      nextRun: nextKey != null && `p-${keyIndex}` === nextKey
    };
  });
}

describe("HeatStrip", () => {
  it("renders a 10,000-partition fixture as a key-order window, not the full set", () => {
    render(<HeatStrip partitions={manyPartitions(10_000)} />);

    const cells = screen.getAllByTestId("heat-cell");
    expect(HEAT_STRIP_WINDOW).toBeLessThan(10_000);
    expect(cells).toHaveLength(HEAT_STRIP_WINDOW);
    expect(cells.length).toBeLessThan(10_000);
    expect(cells[0]).toHaveAttribute("data-key", "p-0");
    expect(cells[HEAT_STRIP_WINDOW - 1]).toHaveAttribute("data-key", `p-${HEAT_STRIP_WINDOW - 1}`);
    expect(cells.some((cell) => cell.getAttribute("data-key") === "p-9999")).toBe(false);
    expect(screen.queryByRole("button", { name: /p-9999/ })).not.toBeInTheDocument();

    const keys = cells.map((cell) => cell.getAttribute("data-key") ?? "");
    const sorted = [...keys].sort((left, right) => left.localeCompare(right, "en", { numeric: true, sensitivity: "base" }));
    expect(keys).toEqual(sorted);
    expect(cells[0]?.getAttribute("data-pressure")).not.toBe("unmeasured");

    const caption = screen.getByTestId("heatstrip-caption");
    expect(caption).toHaveTextContent(/window/i);
    expect(caption).toHaveTextContent(/not the full set/i);
    expect(caption).not.toHaveTextContent(/\bTop\b/);
    expect(screen.queryByTestId("chart-cap")).not.toBeInTheDocument();
    expect(screen.getAllByTestId("heat-query-tick").length).toBeLessThanOrEqual(HEAT_STRIP_WINDOW);
    expect(screen.getByTestId("heat-strip")).toHaveAttribute("data-total", "10000");
    expect(screen.getByTestId("heat-strip")).toHaveAttribute("data-rendered", String(HEAT_STRIP_WINDOW));
    expect(source).not.toMatch(/\b(yAxisId|secondaryAxis|secondaryYAxis|yAxisRight|dualAxis|rightAxis)\b/);
    expect(source).not.toMatch(/from ["']recharts/);
  });

  it("keeps a next-run partition inside the window without mounting the full set", () => {
    render(<HeatStrip partitions={manyPartitions(10_000, "p-5000")} />);

    const cells = screen.getAllByTestId("heat-cell");
    expect(cells).toHaveLength(HEAT_STRIP_WINDOW);
    expect(cells.some((cell) => cell.getAttribute("data-key") === "p-0")).toBe(false);
    const touched = cells.find((cell) => cell.getAttribute("data-key") === "p-5000");
    expect(touched).toHaveAttribute("data-next-run", "true");
    expect(screen.getByRole("button", { name: /next run touches these/ })).toBeInTheDocument();
    expect(screen.getByText("next run touches these")).toBeInTheDocument();
    expect(screen.getByTestId("heatstrip-caption")).toHaveTextContent(/window/i);
    expect(screen.getByTestId("heatstrip-caption")).toHaveTextContent(/not the full set/i);
  });

  it("keeps key order and does not paint missing pressure, queries, or compaction as zero", () => {
    render(
      <HeatStrip
        partitions={[
          {
            id: "later",
            key: "2026-01-02",
            smallFiles: null,
            files: 4,
            queries: null,
            lastCompactionAt: null,
            nextRun: true
          },
          {
            id: "earlier",
            key: "2026-01-01",
            smallFiles: 0,
            files: 3,
            queries: 0,
            lastCompactionAt: "1970-01-01T00:00:00.000Z",
            nextRun: false
          }
        ]}
      />
    );

    const cells = screen.getAllByTestId("heat-cell");
    expect(cells.map((cell) => cell.getAttribute("data-key"))).toEqual(["2026-01-01", "2026-01-02"]);
    expect(cells[0]).toHaveAttribute("data-pressure", "0");
    expect(cells[1]).toHaveAttribute("data-pressure", "unmeasured");
    expect(cells[1]).not.toHaveAttribute("data-pressure", "0");
    expect(screen.getByTitle("2026-01-02 · files 4 · small files Not measured · last compaction Not recorded")).toBeInTheDocument();
    expect(screen.getByTitle("2026-01-01 · files 3 · small files 0 · last compaction Not recorded")).toBeInTheDocument();
    expect(screen.queryByTitle(/1970/)).not.toBeInTheDocument();
    expect(screen.queryByText(/1970/)).not.toBeInTheDocument();
    expect(screen.getAllByText("Not measured").length).toBeGreaterThan(0);
    const ticks = screen.getAllByTestId("heat-query-tick");
    expect(ticks.map((tick) => tick.getAttribute("data-queries"))).toEqual(["0", "unmeasured"]);
    expect(screen.getByTestId("heatstrip-caption")).toHaveTextContent("2 partitions in key order.");
    expect(screen.getByTestId("heatstrip-caption")).not.toHaveTextContent(/not the full set/i);
    expect(document.querySelector("[data-axis='y']")).toBeNull();
  });

  it("says an unpartitioned table has no strip", () => {
    render(<HeatStrip partitions={[]} unpartitioned />);
    expect(within(screen.getByTestId("chart-plot")).getByText("Unpartitioned table")).toBeInTheDocument();
    expect(screen.queryByTestId("heat-cell")).not.toBeInTheDocument();
  });
});
