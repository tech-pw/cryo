import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { FilterBar } from "./FilterBar";

describe("FilterBar", () => {
  it("selects all matching tables from the filter bar", () => {
    const onSelectAllMatching = vi.fn();
    render(
      <FilterBar
        value={{ activeOnly: true }}
        onChange={vi.fn()}
        matchingTotal={13}
        onSelectAllMatching={onSelectAllMatching}
        onClearSelection={vi.fn()}
      />
    );

    fireEvent.click(screen.getByText("Select all matching tables"));
    expect(onSelectAllMatching).toHaveBeenCalledTimes(1);
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
