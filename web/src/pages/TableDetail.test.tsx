import { act, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { MemoryRouter, Route, Routes, useNavigate, useParams } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

// Stand-in for the real shell: holds a per-table draft in local state, like the
// config inventory's staged edits.
vi.mock("./table-detail/TableDetailShell", () => ({
  TableDetailShell: () => {
    const { tableId } = useParams();
    const [draft, setDraft] = useState("");
    return (
      <div>
        <p>table {tableId}</p>
        <p data-testid="draft">{draft || "(none)"}</p>
        <button type="button" onClick={() => setDraft(`staged on ${tableId}`)}>
          stage
        </button>
      </div>
    );
  }
}));

import { TableDetail } from "./TableDetail";

let navigateTo: (path: string) => void = () => undefined;
const NavigateHook = () => {
  navigateTo = useNavigate();
  return null;
};

describe("TableDetail route", () => {
  it("drops per-table drafts when navigating to another table", () => {
    render(
      <MemoryRouter initialEntries={["/tables/a"]}>
        <NavigateHook />
        <Routes>
          <Route path="/tables/:tableId" element={<TableDetail />} />
        </Routes>
      </MemoryRouter>
    );
    fireEvent.click(screen.getByRole("button", { name: "stage" }));
    expect(screen.getByTestId("draft")).toHaveTextContent("staged on a");

    act(() => navigateTo("/tables/b"));
    expect(screen.getByText("table b")).toBeInTheDocument();
    expect(screen.getByTestId("draft")).toHaveTextContent("(none)");
  });
});
