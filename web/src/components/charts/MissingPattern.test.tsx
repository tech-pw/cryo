import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MissingPattern, MissingPatternDefs, MISSING_HATCH_ID, MISSING_MARK_MIN_PX } from "./MissingPattern";
import { plotValue } from "./plotValue";

describe("plotValue", () => {
  it("returns a gap for unknown values and never zero", () => {
    expect(plotValue(null)).toBeNull();
    expect(plotValue(undefined)).toBeNull();
    expect(plotValue(Number.NaN)).toBeNull();
    expect(plotValue(Number.POSITIVE_INFINITY)).toBeNull();
    expect(plotValue(null)).not.toBe(0);
    expect(plotValue(undefined)).not.toBe(0);
    expect(plotValue(Number.NaN)).not.toBe(0);
    expect(plotValue(0)).toBe(0);
    expect(plotValue(4)).toBe(4);
  });
});

describe("MissingPattern", () => {
  it("renders a hatch labelled Not measured, not a zero-height bar", () => {
    render(<MissingPattern />);
    expect(screen.getByText("Not measured")).toBeInTheDocument();
    const hatch = screen.getByTestId("missing-hatch");
    expect(MISSING_MARK_MIN_PX).toBeGreaterThan(0);
    expect(hatch).toHaveStyle({ height: "8px", minHeight: "8px" });
    expect(hatch).not.toHaveStyle({ height: "0px" });
    expect(hatch.style.backgroundImage).toContain("repeating-linear-gradient");
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });

  it("exposes an svg hatch pattern for missing fills", () => {
    const { container } = render(
      <svg>
        <MissingPatternDefs />
      </svg>
    );
    const pattern = container.querySelector("pattern");
    expect(pattern).not.toBeNull();
    expect(pattern?.id).toBe(MISSING_HATCH_ID);
    expect(pattern?.getAttribute("height")).not.toBe("0");
  });
});
