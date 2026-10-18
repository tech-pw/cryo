import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { FilterBar } from "./FilterBar";

describe("FilterBar", () => {
  it("selects all matching tables from the filter bar", () => {
    const onSelectAllMatching = vi.fn();
    render(
      <FilterBar
        value={{ activeOnly: true, grades: ["D"] }}
        onChange={vi.fn()}
        matchingTotal={13}
        onSelectAllMatching={onSelectAllMatching}
        onClearSelection={vi.fn()}
      />
    );

    fireEvent.click(screen.getByText("Select all matching tables"));
    expect(onSelectAllMatching).toHaveBeenCalledTimes(1);
  });

  it("renders catalog filter when options are provided", () => {
    render(
      <FilterBar
        value={{ activeOnly: true, sourceIds: ["catalog-1"] }}
        onChange={vi.fn()}
        catalogOptions={[
          { label: "Prod", value: "catalog-1" },
          { label: "Staging", value: "catalog-2" }
        ]}
        matchingTotal={13}
        onSelectAllMatching={vi.fn()}
        onClearSelection={vi.fn()}
      />
    );

    expect(screen.getByRole("button", { name: "Filter by catalog" })).toHaveTextContent("Catalog: Prod");
    expect(screen.getByRole("searchbox", { name: "Schema or table search" })).toBeInTheDocument();
    expect(screen.getByText("13 tables match")).toBeInTheDocument();
    expect(screen.getByText("Filtered view")).toBeInTheDocument();
  });

  it("shows default scope label when only active tables are included", () => {
    render(
      <FilterBar
        value={{ activeOnly: true }}
        onChange={vi.fn()}
        matchingTotal={42}
        onSelectAllMatching={vi.fn()}
        onClearSelection={vi.fn()}
      />
    );

    expect(screen.getByText("Active tables")).toBeInTheDocument();
    expect(screen.getByText("42 tables match")).toBeInTheDocument();
    expect(screen.queryByText("all active tables")).not.toBeInTheDocument();
  });

  it("shows filter selection state and clear action", () => {
    const onClearSelection = vi.fn();
    render(
      <FilterBar
        value={{ activeOnly: true }}
        onChange={vi.fn()}
        matchingTotal={13}
        hasSelection
        filterSelectionActive
        onSelectAllMatching={vi.fn()}
        onClearSelection={onClearSelection}
      />
    );

    expect(screen.getByText("Filter selection")).toBeInTheDocument();
    expect(screen.queryByText("Select all matching tables")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Clear selection"));
    expect(onClearSelection).toHaveBeenCalledTimes(1);
  });

  it("shows clear selection for id mode without filter tag", () => {
    render(
      <FilterBar
        value={{ activeOnly: true }}
        onChange={vi.fn()}
        matchingTotal={13}
        hasSelection
        onSelectAllMatching={vi.fn()}
        onClearSelection={vi.fn()}
      />
    );

    expect(screen.queryByText("Filter selection")).not.toBeInTheDocument();
    expect(screen.getByText("Clear selection")).toBeInTheDocument();
    expect(screen.getByText("Select all matching tables")).toBeInTheDocument();
  });
});
