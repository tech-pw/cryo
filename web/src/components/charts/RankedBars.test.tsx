import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RankedBars } from "./RankedBars";

describe("RankedBars", () => {
  it("shows the full name and does not plot a missing value as zero", () => {
    render(
      <RankedBars
        title="Top partitions"
        unit="files"
        total={20}
        items={[
          { id: "a", label: "a-very-long-partition-name", value: 4 },
          { id: "b", label: "missing", value: null }
        ]}
      />
    );
    expect(screen.getByTitle("a-very-long-partition-name")).toBeInTheDocument();
    expect(screen.getAllByText("Not measured").length).toBeGreaterThan(0);
    expect(screen.getByText(/Top 2 of 20/i)).toBeInTheDocument();
  });
});
