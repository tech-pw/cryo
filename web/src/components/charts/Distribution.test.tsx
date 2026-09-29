import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import source from "./Distribution.tsx?raw";
import { Distribution } from "./Distribution";

const MIB = 2 ** 20;

describe("Distribution", () => {
  it("keeps data and delete files apart, and does not plot unknown as a bar or a zero", () => {
    render(
      <Distribution
        writerTargetBytes={512 * MIB}
        before={{
          scannedAt: "2026-09-01T00:00:00Z",
          dataFiles: {
            bins: [
              { lowerBytes: MIB, count: 9 },
              { lowerBytes: 4 * MIB, count: 2 }
            ],
            unknown: 0
          },
          deleteFiles: {
            bins: [{ lowerBytes: MIB, count: 1 }],
            unknown: 0
          }
        }}
        scan={{
          scannedAt: "2026-09-28T00:00:00Z",
          fileCount: 12,
          dataFiles: {
            bins: [
              { lowerBytes: MIB, count: 4 },
              { lowerBytes: 2 * MIB, count: null },
              { lowerBytes: 4 * MIB, count: 1 }
            ],
            unknown: 3
          },
          deleteFiles: {
            bins: [
              { lowerBytes: MIB, count: 0 },
              { lowerBytes: 2 * MIB, count: 2 },
              { lowerBytes: 4 * MIB, count: null }
            ],
            unknown: null
          }
        }}
      />
    );

    const plots = screen.getAllByTestId("distribution-plot");
    expect(plots.map((plot) => plot.getAttribute("data-kind"))).toEqual(["data", "delete"]);
    expect(plots[0]?.querySelector("[data-kind='delete']")).toBeNull();
    expect(plots[1]?.querySelector("[data-kind='data']")).toBeNull();

    const bars = screen.getAllByTestId("distribution-bar");
    expect(bars.some((bar) => bar.getAttribute("data-kind") === "data" && bar.getAttribute("data-series") === "after" && bar.getAttribute("data-value") === "4")).toBe(true);
    expect(bars.some((bar) => bar.getAttribute("data-kind") === "data" && bar.getAttribute("data-series") === "before" && bar.getAttribute("data-value") === "9")).toBe(true);
    expect(bars.some((bar) => bar.getAttribute("data-kind") === "delete" && bar.getAttribute("data-value") === "0" && bar.getAttribute("data-lower") === String(MIB))).toBe(true);
    expect(bars.some((bar) => bar.getAttribute("data-lower") === String(2 * MIB) && bar.getAttribute("data-series") === "before" && bar.getAttribute("data-value") === "0")).toBe(false);
    expect(bars.some((bar) => bar.getAttribute("data-lower") === String(2 * MIB) && bar.getAttribute("data-kind") === "data" && bar.getAttribute("data-series") === "after" && bar.getAttribute("data-value") === "0")).toBe(false);
    expect(new Set(bars.filter((bar) => bar.getAttribute("data-kind") === "data").map((bar) => bar.getAttribute("data-series"))).size).toBeLessThanOrEqual(2);

    const unmeasured = screen.getAllByTestId("distribution-unmeasured");
    expect(unmeasured.length).toBeGreaterThan(0);
    expect(unmeasured.every((node) => node.getAttribute("data-value") === "unmeasured")).toBe(true);
    expect(unmeasured.every((node) => node.getAttribute("data-value") !== "0")).toBe(true);

    const unknown = screen.getByTestId("distribution-unknown");
    expect(unknown).toHaveTextContent("Not measured");
    expect(unknown).toHaveTextContent("3");
    expect(unknown.querySelector("[data-testid='distribution-bar']")).toBeNull();
    expect(screen.queryByTestId("distribution-unknown-bar")).not.toBeInTheDocument();

    const tick = screen.getByTestId("writer-target-tick");
    expect(tick).toHaveAttribute("data-axis", "x");
    expect(tick).toHaveTextContent("target 512 MiB");
    expect(Number(tick.getAttribute("data-x"))).toBeGreaterThan(0);
    expect(tick.querySelector("[data-testid='distribution-bar']")).toBeNull();
    expect(document.querySelector("[data-axis='y']")).toBeNull();
    expect(screen.getAllByTestId("distribution-x-axis")).toHaveLength(1);

    const axis = screen.getByTestId("distribution-x-axis");
    expect(within(axis).getByText("1 MiB")).toBeInTheDocument();
    expect(within(axis).getByText("2 MiB")).toBeInTheDocument();
    expect(within(axis).getByText("4 MiB")).toBeInTheDocument();
    expect(screen.getByTestId("distribution-caption")).toHaveTextContent("12 files");
    expect(screen.getByTestId("distribution-caption")).toHaveTextContent("scanned 28 Sep 2026");
    expect(screen.getByTestId("distribution-caption")).toHaveTextContent("writer target 512 MiB");
    expect(screen.getByTestId("distribution-legend")).toHaveTextContent("before");
    expect(screen.getByTestId("distribution-legend")).toHaveTextContent("after");
    expect(source).not.toMatch(/\b(yAxisId|secondaryAxis|secondaryYAxis|yAxisRight|dualAxis|rightAxis)\b/);
    expect(source).not.toMatch(/from ["']recharts/);
  });

  it("does not turn a missing scan time or writer target into epoch zero", () => {
    render(
      <Distribution
        scan={{
          scannedAt: "1970-01-01T00:00:00.000Z",
          dataFiles: { bins: [{ lowerBytes: MIB, count: 1 }], unknown: 0 },
          deleteFiles: { bins: [{ lowerBytes: MIB, count: 2 }], unknown: 0 }
        }}
        writerTargetBytes={null}
      />
    );

    expect(screen.getByTestId("distribution-caption")).toHaveTextContent("scanned Not recorded");
    expect(screen.getByTestId("distribution-caption")).toHaveTextContent("writer target Not measured");
    expect(screen.queryByTestId("writer-target-tick")).not.toBeInTheDocument();
    expect(screen.queryByText(/1970/)).not.toBeInTheDocument();
    expect(screen.queryByText(/target 0/)).not.toBeInTheDocument();
    expect(screen.getAllByTestId("distribution-bar").every((bar) => bar.getAttribute("data-series") === "after")).toBe(true);
  });
});
