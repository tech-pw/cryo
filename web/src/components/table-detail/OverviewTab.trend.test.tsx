import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HealthScoreTrend } from "./OverviewTab";

describe("HealthScoreTrend", () => {
  it("leaves a null score as a gap instead of painting zero", () => {
    render(
      <HealthScoreTrend
        points={[
          { timestamp: Date.parse("2026-09-01T00:00:00Z"), score: null, grade: null },
          { timestamp: Date.parse("2026-09-02T00:00:00Z"), score: 72, grade: "C" }
        ]}
      />
    );

    expect(screen.getByText("Gaps are not measured.")).toBeInTheDocument();
    expect(screen.getAllByText("Not measured").length).toBeGreaterThan(0);
    expect(screen.getByText("72.0")).toBeInTheDocument();
    expect(screen.queryByText("0.0")).not.toBeInTheDocument();
    expect(document.getElementById("scoreGrad2")).not.toBeInTheDocument();
  });
});
