import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RankedTableBlock } from "./RankedTableBlock";

describe("RankedTableBlock", () => {
  it("renders an unknown cell as not zero", () => {
    render(
      <RankedTableBlock
        block={{
          type: "ranked_table",
          title: "Small files",
          columns: [
            { key: "table", header: "Table" },
            { key: "small_files", header: "Small files" }
          ],
          rows: [
            { id: "known", table: "events", small_files: 0 },
            { id: "unknown", table: "orders", small_files: null }
          ]
        }}
      />
    );

    const unknown = screen.getByRole("row", { name: /orders/ });
    expect(within(unknown).getByText("-")).toBeInTheDocument();
    expect(within(unknown).queryByText("0")).not.toBeInTheDocument();
  });
});
