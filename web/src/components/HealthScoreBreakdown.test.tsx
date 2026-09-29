import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { buildHealthScoreFactors, HealthScoreBreakdown } from "./HealthScoreBreakdown";

describe("HealthScoreBreakdown", () => {
  it("treats a missing health input as Unknown, not a green zero", () => {
    const unscanned = buildHealthScoreFactors(null, null, null);
    expect(unscanned.every((factor) => factor.level === "unknown")).toBe(true);
    expect(unscanned.every((factor) => factor.detail === "Not scanned")).toBe(true);
    expect(unscanned.some((factor) => factor.level === "ok")).toBe(false);

    const partial = buildHealthScoreFactors(
      {
        small_files_count: undefined,
        total_files: 10,
        partition_skew_ratio: undefined,
        manifest_count: 0,
        total_snapshots: 1
      },
      null,
      100
    );
    expect(partial.find((factor) => factor.label === "Small-file pressure")).toMatchObject({
      level: "unknown",
      detail: "Not measured"
    });
    expect(partial.find((factor) => factor.label === "Partition skew")?.level).toBe("unknown");
    expect(partial.find((factor) => factor.label === "Manifest bloat")?.level).toBe("ok");

    render(<HealthScoreBreakdown factors={unscanned} />);
    expect(screen.getByText("diagnostic, not score")).toBeInTheDocument();
    expect(screen.getAllByText("Not scanned")).toHaveLength(4);
    expect(screen.queryByText("0% of files")).not.toBeInTheDocument();
    expect(document.querySelector(".td-part-bar")).not.toBeInTheDocument();
  });

  it("lists persisted point deductions instead of drawing factor bars as the grade", () => {
    render(
      <HealthScoreBreakdown
        factors={buildHealthScoreFactors(null, null, null)}
        deductions={[
          { label: "Small files", deduction: 20, severity: "critical", reason: "62% small" },
          { label: "Snapshots", deduction: Number.NaN, severity: "warning", reason: "" }
        ]}
      />
    );

    expect(screen.getByText("Why this grade")).toBeInTheDocument();
    expect(screen.getByText("Small files")).toBeInTheDocument();
    expect(screen.getByText("-20 pts")).toBeInTheDocument();
    expect(screen.getByText("Not measured")).toBeInTheDocument();
    expect(screen.queryByText("diagnostic, not score")).not.toBeInTheDocument();
    expect(screen.queryByText("Not scanned")).not.toBeInTheDocument();
    expect(document.querySelector(".td-part-bar")).not.toBeInTheDocument();
  });
});
