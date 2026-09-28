import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SchemaTab } from "./SchemaTab";

describe("SchemaTab", () => {
  it("reads an unknown null rate as Not measured, not 0%", () => {
    render(
      <SchemaTab
        loading={false}
        columns={[
          { name: "id", type: "bigint" },
          { name: "note", type: "varchar", nullRate: 0.1 }
        ]}
      />
    );

    expect(screen.getByText("Not measured")).toBeInTheDocument();
    expect(screen.getByText("10.0% Null")).toBeInTheDocument();
    expect(screen.queryByText("0.0% Null")).not.toBeInTheDocument();
  });
});
