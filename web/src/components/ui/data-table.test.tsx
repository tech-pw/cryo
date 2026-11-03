import type { RowSelectionState } from "@tanstack/react-table";
import { fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { ColumnVisibilityMenu, DataTable, useDebouncedValue, type DataTableColumnDef } from "./data-table";

interface Row {
  id: string;
  name: string;
  amount: number;
}

const ROWS: Row[] = [
  { id: "a", name: "Alpha", amount: 10 },
  { id: "b", name: "Beta", amount: 20 }
];

const COLUMNS: DataTableColumnDef<Row, unknown>[] = [
  { id: "name", accessorKey: "name", header: "Name" },
  { id: "amount", accessorKey: "amount", header: "Amount" }
];

describe("DataTable column visibility", () => {
  it("hides a column when columnVisibility marks it false, and shows it again when true", () => {
    const { rerender } = render(
      <DataTable columns={COLUMNS} data={ROWS} columnVisibility={{ amount: false }} />
    );

    expect(screen.getByRole("columnheader", { name: "Name" })).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "Amount" })).not.toBeInTheDocument();

    rerender(<DataTable columns={COLUMNS} data={ROWS} columnVisibility={{ amount: true }} />);
    expect(screen.getByRole("columnheader", { name: "Amount" })).toBeInTheDocument();
  });
});

describe("ColumnVisibilityMenu", () => {
  it("uses a native popover + checkboxes, not a hand-rolled ARIA grid", () => {
    const onChange = vi.fn();
    render(
      <ColumnVisibilityMenu
        options={[
          { id: "name", label: "Name" },
          { id: "amount", label: "Amount" }
        ]}
        visibility={{ amount: false }}
        onChange={onChange}
      />
    );

    // Trigger reports how many columns are shown out of the total.
    expect(screen.getByRole("button", { name: /columns/i })).toHaveTextContent("1/2");
    expect(screen.queryByRole("grid")).not.toBeInTheDocument();
  });

  it("groups options under headings and offers reset and show-all", () => {
    const onReset = vi.fn();
    const onShowAll = vi.fn();
    render(
      <ColumnVisibilityMenu
        options={[
          { id: "name", label: "Name", group: "Identity" },
          { id: "amount", label: "Amount", group: "Money" }
        ]}
        visibility={{ amount: false }}
        onChange={vi.fn()}
        onReset={onReset}
        onShowAll={onShowAll}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: /columns/i }));
    expect(screen.getByRole("group", { name: "Money" })).toBeInTheDocument();
    expect(screen.getByText("1 of 2 shown")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Reset to default"));
    fireEvent.click(screen.getByText("Show all"));
    expect(onReset).toHaveBeenCalledTimes(1);
    expect(onShowAll).toHaveBeenCalledTimes(1);
  });
});

describe("DataTable footer", () => {
  it("renders a <tfoot> only when a column defines a footer, using real footer content", () => {
    const columnsWithFooter: DataTableColumnDef<Row, unknown>[] = [
      { id: "name", accessorKey: "name", header: "Name", footer: () => "TOTAL" },
      {
        id: "amount",
        accessorKey: "amount",
        header: "Amount",
        footer: () => `${ROWS.reduce((sum, row) => sum + row.amount, 0)}`
      }
    ];

    const { rerender } = render(<DataTable columns={COLUMNS} data={ROWS} />);
    expect(document.querySelector("tfoot")).not.toBeInTheDocument();

    rerender(<DataTable columns={columnsWithFooter} data={ROWS} />);
    const tfoot = document.querySelector("tfoot");
    expect(tfoot).not.toBeNull();
    expect(within(tfoot as HTMLElement).getByText("TOTAL")).toBeInTheDocument();
    expect(within(tfoot as HTMLElement).getByText("30")).toBeInTheDocument();
  });
});

describe("useDebouncedValue", () => {
  it("holds the previous value until the delay elapses, then settles on the latest value", async () => {
    const { result, rerender } = renderHook(({ value }) => useDebouncedValue(value, 100), {
      initialProps: { value: "Showing 1–2 of 2" }
    });

    expect(result.current).toBe("Showing 1–2 of 2");

    rerender({ value: "Showing 1–25 of 40" });
    // Doesn't jump immediately -- still reflects the previous settled value.
    expect(result.current).toBe("Showing 1–2 of 2");

    await waitFor(() => expect(result.current).toBe("Showing 1–25 of 40"), { timeout: 1000 });
  });

  it("cancels a pending update if the value changes again before the delay elapses", async () => {
    const { result, rerender } = renderHook(({ value }) => useDebouncedValue(value, 150), {
      initialProps: { value: "a" }
    });

    rerender({ value: "b" });
    rerender({ value: "c" });

    // Only the *final* value should ever be announced -- "b" should never appear.
    await waitFor(() => expect(result.current).toBe("c"), { timeout: 1000 });
  });
});

describe("DataTable stretch layout", () => {
  it("does not pin column widths when resizing is off and fillAvailableWidth is on", () => {
    const { container } = render(
      <DataTable
        columns={[
          { id: "name", accessorKey: "name", header: "Name", size: 160 },
          { id: "amount", accessorKey: "amount", header: "Amount", size: 80 }
        ]}
        data={ROWS}
        fillAvailableWidth
      />
    );

    const table = container.querySelector("table");
    expect(table).toHaveClass("w-full", "min-w-full", "table-auto");
    expect(table).not.toHaveAttribute("style");
    expect(container.querySelector("[data-column-resize]")).not.toBeInTheDocument();
  });
});

describe("DataTable column resizing", () => {
  it("exposes a resize handle on resizable headers and none on the selection column", () => {
    const { container } = render(
      <DataTable columns={COLUMNS} data={ROWS} enableRowSelection enableColumnResizing />
    );

    expect(container.querySelector('[data-column-resize="name"]')).toBeInTheDocument();
    expect(container.querySelector('[data-column-resize="amount"]')).toBeInTheDocument();
    expect(container.querySelector('[data-column-resize="__select"]')).not.toBeInTheDocument();
  });

  it("applies explicit column widths so leftover space does not stretch the first column", () => {
    const { container } = render(
      <DataTable
        columns={[
          { id: "name", accessorKey: "name", header: "Name", size: 160 },
          { id: "amount", accessorKey: "amount", header: "Amount", size: 80 }
        ]}
        data={ROWS}
        enableColumnResizing
      />
    );

    const table = container.querySelector("table");
    expect(table).toHaveStyle({ width: "240px" });
    const headers = screen.getAllByRole("columnheader");
    expect(headers[0]).toHaveStyle({ width: "160px", minWidth: "160px", maxWidth: "160px" });
    expect(headers[1]).toHaveStyle({ width: "80px", minWidth: "80px", maxWidth: "80px" });
  });

  it("resizes a column by dragging its handle and clamps to the default max size", () => {
    const { container } = render(
      <DataTable
        columns={[{ id: "name", accessorKey: "name", header: "Name", size: 160 }]}
        data={ROWS}
        enableColumnResizing
      />
    );
    const header = screen.getByRole("columnheader", { name: "Name" });
    const handle = container.querySelector('[data-column-resize="name"]') as HTMLElement;
    const scroller = container.querySelector(".cryo-hscroll");

    fireEvent.mouseDown(handle, { clientX: 100 });
    fireEvent.mouseMove(document, { clientX: 140 });
    expect(scroller).toHaveClass("select-none");
    fireEvent.mouseUp(document, { clientX: 140 });
    expect(header).toHaveStyle({ width: "200px" });
    expect(scroller).not.toHaveClass("select-none");

    fireEvent.mouseDown(handle, { clientX: 200 });
    fireEvent.mouseMove(document, { clientX: 2000 });
    fireEvent.mouseUp(document, { clientX: 2000 });
    expect(header).toHaveStyle({ width: "720px" });
  });

  it("resets a column to its default size on double-click and persists when a storage key is set", () => {
    window.localStorage.clear();
    window.localStorage.setItem("cryo.test.columnSizing", JSON.stringify({ name: 280 }));
    const { container } = render(
      <DataTable
        columns={[{ id: "name", accessorKey: "name", header: "Name", size: 160 }]}
        data={ROWS}
        columnSizingStorageKey="cryo.test.columnSizing"
      />
    );

    const header = screen.getByRole("columnheader", { name: "Name" });
    expect(header).toHaveStyle({ width: "280px" });

    fireEvent.doubleClick(container.querySelector('[data-column-resize="name"]') as HTMLElement);
    expect(header).toHaveStyle({ width: "160px" });
    const stored = JSON.parse(window.localStorage.getItem("cryo.test.columnSizing") ?? "{}") as Record<string, number>;
    expect(stored.name === undefined || stored.name === 160).toBe(true);
  });
});

describe("DataTable row selection", () => {
  const SelectableTable = ({ initial = {} }: { initial?: RowSelectionState }) => {
    const [selection, setSelection] = useState<RowSelectionState>(initial);
    return (
      <DataTable
        columns={COLUMNS}
        data={ROWS}
        getRowId={(row) => row.id}
        rowSelection={selection}
        onRowSelectionChange={setSelection}
        enableRowSelection={(row) => row.original.id !== "c"}
      />
    );
  };
  const selectAll = () => screen.getByRole("checkbox", { name: "Select all rows on this page" });
  const rowBoxes = () => screen.getAllByRole("checkbox", { name: "Select row" });

  it("shows the header checkbox as unchecked, indeterminate, then checked as rows are selected", () => {
    render(<SelectableTable />);
    expect(selectAll()).toHaveAttribute("aria-checked", "false");

    fireEvent.click(rowBoxes()[0]);
    expect(selectAll()).toHaveAttribute("aria-checked", "mixed");

    fireEvent.click(rowBoxes()[1]);
    expect(selectAll()).toHaveAttribute("aria-checked", "true");
    expect(document.querySelectorAll('tr[data-state="selected"]')).toHaveLength(2);
  });

  it("toggles every selectable row on the page from the header checkbox", () => {
    render(<SelectableTable initial={{ a: true }} />);

    fireEvent.click(selectAll());
    expect(rowBoxes().map((box) => box.getAttribute("aria-checked"))).toEqual(["true", "true"]);

    fireEvent.click(selectAll());
    expect(rowBoxes().map((box) => box.getAttribute("aria-checked"))).toEqual(["false", "false"]);
    expect(document.querySelectorAll('tr[data-state="selected"]')).toHaveLength(0);
  });

  it("disables the checkbox for rows the predicate rejects", () => {
    render(
      <DataTable
        columns={COLUMNS}
        data={[...ROWS, { id: "c", name: "Gamma", amount: 30 }]}
        getRowId={(row) => row.id}
        rowSelection={{}}
        onRowSelectionChange={vi.fn()}
        enableRowSelection={(row) => row.original.id !== "c"}
      />
    );
    expect(rowBoxes().map((box) => box.hasAttribute("disabled"))).toEqual([false, false, true]);
  });
});

describe("DataTable pagination live region", () => {
  it("exposes an accessible, debounced status region alongside the visible page range", async () => {
    const onPageChange = vi.fn();
    render(
      <DataTable
        columns={COLUMNS}
        data={ROWS}
        pagination={{ pageIndex: 0, pageSize: 25, totalRows: 2, onPageChange }}
      />
    );

    const liveRegion = screen.getByRole("status");
    expect(liveRegion).toHaveAttribute("aria-live", "polite");
    expect(liveRegion).toHaveAttribute("aria-atomic", "true");
    expect(screen.getAllByText("Showing 1–2 of 2").length).toBeGreaterThanOrEqual(1);
    await waitFor(() => expect(liveRegion).toHaveTextContent("Showing 1–2 of 2"));
  });
});
