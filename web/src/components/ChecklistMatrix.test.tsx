import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ChecklistCoverageNote, checklistCoverage } from "./ChecklistMatrix";

describe("checklistCoverage", () => {
  it("says all green only when every check is known and green", () => {
    expect(checklistCoverage({ green: 12, yellow: 0, red: 0, unknown: 0 }).allGreen).toBe(true);
    expect(checklistCoverage({ green: 10, yellow: 0, red: 0, unknown: 2 }).allGreen).toBe(false);
    expect(checklistCoverage({ green: 10, yellow: 2, red: 0, unknown: 0 }).allGreen).toBe(false);
    expect(checklistCoverage({ green: 0, yellow: 0, red: 0, unknown: 0 }).label).toBe("Not measured");
    expect(checklistCoverage({ green: 8, yellow: 1, red: 0, unknown: 3 }).label).toBe(
      "Checked 9 of 12 · 3 unknown"
    );
  });

  it("does not render an all-green badge when a check is unknown", () => {
    render(<ChecklistCoverageNote counts={{ green: 4, yellow: 0, red: 0, unknown: 1 }} />);
    expect(screen.getByText("Checked 4 of 5 · 1 unknown")).toBeInTheDocument();
    expect(screen.queryByText("all green")).not.toBeInTheDocument();
  });
});
