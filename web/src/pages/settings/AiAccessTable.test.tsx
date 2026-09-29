import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AiAccessTable } from "./AiAccessTable";

// Source of role rows: cryo/api/extensions_routes/ai_features.py
describe("AiAccessTable", () => {
  it("lists viewer and editor routes and has no edit control", () => {
    render(<AiAccessTable />);

    const table = screen.getByRole("table");
    expect(screen.getByText("Read-only")).toBeInTheDocument();
    expect(within(table).getAllByText("viewer").length).toBeGreaterThan(0);
    expect(within(table).getByText("editor")).toBeInTheDocument();
    expect(within(table).getByText("/api/v1/ai/runs/diagnoses/{diagnosis_id}/feedback")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /edit/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });
});
