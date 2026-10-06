import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StructureStatsHistory } from "./StructureTabPanel";

describe("StructureStatsHistory", () => {
  it("keeps an unknown small-file count and size as Not measured", () => {
    render(
      <StructureStatsHistory
        points={[{ timestamp: Date.parse("2026-09-01T00:00:00Z"), small_files_count: null, total_size_gb: null }]}
      />
    );

    expect(screen.getByRole("heading", { name: "Small-file count" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Size (GB)" })).toBeInTheDocument();
    expect(screen.getAllByText("Not measured").length).toBeGreaterThan(0);
    expect(screen.queryByText("0.0 GB")).not.toBeInTheDocument();
    expect(screen.queryByText(/^0$/)).not.toBeInTheDocument();
  });
});
