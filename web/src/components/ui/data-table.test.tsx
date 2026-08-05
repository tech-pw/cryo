import type { ColumnDef } from "@tanstack/react-table";
import { render, renderHook, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ColumnVisibilityMenu, DataTable, useDebouncedValue } from "./data-table";

interface Row {
  id: string;
  name: string;
  amount: number;
}

const ROWS: Row[] = [
  { id: "a", name: "Alpha", amount: 10 },
  { id: "b", name: "Beta", amount: 20 }
];

const COLUMNS: ColumnDef<Row, unknown>[] = [
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

    // Trigger reports how many columns are currently hidden.
    expect(screen.getByRole("button", { name: /columns/i })).toHaveTextContent("(1 hidden)");
    expect(screen.queryByRole("grid")).not.toBeInTheDocument();
  });
});

describe("DataTable footer", () => {
  it("renders a <tfoot> only when a column defines a footer, using real footer content", () => {
    const columnsWithFooter: ColumnDef<Row, unknown>[] = [
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
