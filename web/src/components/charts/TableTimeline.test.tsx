import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import source from "./TableTimeline.tsx?raw";
import { TableTimeline } from "./TableTimeline";

describe("TableTimeline", () => {
  it("plots writes, reads, and Cryo actions on one time axis", () => {
    render(
      <TableTimeline
        writes={[
          { id: "w-missing", at: null, label: "append" },
          { id: "w-real", at: "2026-09-01T00:00:00Z", label: "append" }
        ]}
        reads={[
          { id: "r-missing", at: null, queries: 4 },
          { id: "r-unknown", at: "2026-09-15T00:00:00Z", queries: null },
          { id: "r-zero", at: "2026-09-16T00:00:00Z", queries: 0 }
        ]}
        actions={[
          { id: "a-missing", at: null, label: "optimize" },
          { id: "a-real", at: "2026-09-30T00:00:00Z", label: "optimize" }
        ]}
      />
    );

    expect(screen.getAllByTestId("timeline-lane").map((lane) => lane.getAttribute("data-lane"))).toEqual([
      "writes",
      "reads",
      "cryo"
    ]);
    const plot = screen.getByTestId("chart-plot");
    expect(within(plot).getByText("Writes")).toBeInTheDocument();
    expect(within(plot).getByText("Reads")).toBeInTheDocument();
    expect(within(plot).getByText("Cryo")).toBeInTheDocument();
    expect(screen.getAllByTestId("timeline-axis")).toHaveLength(1);
    expect(screen.getByTestId("timeline-axis")).toHaveAttribute("data-axis", "time");
    expect(screen.getByTestId("timeline-axis")).toHaveAttribute("data-scale-type", "time");

    const start = String(Date.parse("2026-09-01T00:00:00Z"));
    const lanes = screen.getAllByTestId("timeline-lane");
    expect(new Set(lanes.map((lane) => lane.getAttribute("data-scale-start")))).toEqual(new Set([start]));
    expect(screen.getByTestId("timeline-axis")).toHaveAttribute("data-start", start);
    expect(screen.getByTestId("timeline-axis")).not.toHaveAttribute("data-start", "0");

    const marks = screen.getAllByTestId("timeline-mark");
    expect(marks.every((mark) => mark.getAttribute("data-epoch") !== "0")).toBe(true);
    expect(marks.every((mark) => Number(mark.getAttribute("data-epoch")) > 0)).toBe(true);
    expect(document.querySelector("[data-id='w-missing']")).toBeNull();
    expect(document.querySelector("[data-id='r-missing']")).toBeNull();
    expect(document.querySelector("[data-id='a-missing']")).toBeNull();

    const x = (id: string) => Number(document.querySelector(`[data-id='${id}']`)?.getAttribute("data-x"));
    expect(x("r-unknown")).toBeGreaterThan(x("w-real"));
    expect(x("r-unknown")).toBeLessThan(x("a-real"));
    expect(document.querySelector("[data-id='r-unknown']")).toHaveAttribute("data-value", "unmeasured");
    expect(document.querySelector("[data-id='r-zero']")).toHaveAttribute("data-value", "0");
    expect(document.querySelector("[data-id='r-zero']")).toHaveAttribute("title", "0 queries");

    expect(screen.getAllByText("Not recorded").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Not measured").length).toBeGreaterThan(0);
    expect(screen.queryByText(/1970/)).not.toBeInTheDocument();
    expect(document.querySelector("[data-axis='y']")).toBeNull();
    expect(source).not.toMatch(/\b(yAxisId|secondaryAxis|secondaryYAxis|yAxisRight|dualAxis|rightAxis)\b/);
    expect(source).not.toMatch(/from ["']recharts/);
  });

  it("does not plot a missing timestamp at epoch zero", () => {
    render(
      <TableTimeline
        writes={[]}
        reads={[]}
        actions={[{ id: "a0", at: "1970-01-01T00:00:00.000Z", label: "optimize" }]}
      />
    );

    expect(screen.getAllByText("Not recorded").length).toBeGreaterThan(0);
    expect(screen.queryByTestId("timeline-mark")).not.toBeInTheDocument();
    expect(screen.getByTestId("timeline-axis")).not.toHaveAttribute("data-start");
    expect(screen.getByTestId("timeline-axis")).not.toHaveAttribute("data-start", "0");
    expect(screen.queryByText(/1970/)).not.toBeInTheDocument();
    expect(screen.queryByTitle(/1970/)).not.toBeInTheDocument();
  });
});
