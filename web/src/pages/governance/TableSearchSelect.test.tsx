import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TableSearchSelect } from "./TableSearchSelect";

vi.mock("../../lib/auth", () => ({
  useAuth: () => ({
    api: { token: "test-token" },
    token: "test-token",
    role: "operator",
    subject: "tester",
    loading: false,
    error: null,
    isAuthenticated: true,
    signIn: vi.fn(),
    signOut: vi.fn()
  })
}));

const listTables = vi.fn();
const getTable = vi.fn();

vi.mock("../../api/client", () => ({
  listTables: (...args: unknown[]) => listTables(...args),
  getTable: (...args: unknown[]) => getTable(...args)
}));

const renderSelect = (props: Partial<React.ComponentProps<typeof TableSearchSelect>> = {}) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const onChange = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <TableSearchSelect value="" onChange={onChange} {...props} />
    </QueryClientProvider>
  );
  return { onChange };
};

describe("TableSearchSelect", () => {
  beforeEach(() => {
    listTables.mockReset();
    getTable.mockReset();
    listTables.mockResolvedValue({
      items: [
        { id: "t1", source_id: "s1", schema_name: "demo", table_name: "orders" },
        { id: "t2", source_id: "s1", schema_name: "demo", table_name: "customers" }
      ],
      total: 2
    });
    getTable.mockResolvedValue(undefined);
  });

  it("loads the initial (unfiltered) table list with an honest count", async () => {
    renderSelect();
    await waitFor(() => expect(listTables).toHaveBeenCalled());
    expect(listTables).toHaveBeenCalledWith(
      { token: "test-token" },
      expect.objectContaining({ predicate: null, limit: 25, offset: 0, sort: "table_name" })
    );
    expect(await screen.findByText(/Showing 2 of 2 tables/)).toBeInTheDocument();
  }, 10000);

  it("debounces search input and queries by name/schema glob predicate", async () => {
    renderSelect();
    await waitFor(() => expect(listTables).toHaveBeenCalledTimes(1));
    listTables.mockResolvedValue({
      items: [{ id: "t1", source_id: "s1", schema_name: "demo", table_name: "orders" }],
      total: 1
    });

    fireEvent.change(screen.getByPlaceholderText("Search tables by name…"), {
      target: { value: "orders" }
    });

    // Not yet debounced (still within the 250ms window).
    expect(listTables).toHaveBeenCalledTimes(1);

    await waitFor(() => expect(listTables).toHaveBeenCalledTimes(2), { timeout: 2000 });
    const [, options] = listTables.mock.calls[1] as [unknown, { predicate: unknown }];
    expect(JSON.stringify(options.predicate)).toContain("orders");
    expect(await screen.findByText(/1 table matches "orders"/)).toBeInTheDocument();
  }, 10000);

  it("keeps the selected table's label visible even when it falls outside the search window", async () => {
    getTable.mockResolvedValue({
      id: "t9",
      source_id: "s1",
      schema_name: "archive",
      table_name: "old_table"
    });
    renderSelect({ value: "t9" });

    expect(await screen.findByText("archive.old_table")).toBeInTheDocument();
    expect(getTable).toHaveBeenCalledWith({ token: "test-token" }, "t9");
  }, 10000);

  it("does not fetch a selected table by id when no table is selected", async () => {
    renderSelect({ value: "" });
    await waitFor(() => expect(listTables).toHaveBeenCalled());
    expect(getTable).not.toHaveBeenCalled();
  }, 10000);
});
