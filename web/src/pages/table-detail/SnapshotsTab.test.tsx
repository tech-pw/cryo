import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { TableSnapshotsResponse } from "../../api/snapshots";
import { SnapshotsTab } from "./SnapshotsTab";

const { listTableSnapshots, protectTableSnapshot, rollbackTableSnapshot, listProtectedSnapshots, authState } =
  vi.hoisted(() => ({
    listTableSnapshots: vi.fn(),
    protectTableSnapshot: vi.fn(),
    rollbackTableSnapshot: vi.fn(),
    listProtectedSnapshots: vi.fn(),
    authState: { role: "viewer" }
  }));

vi.mock("../../api/snapshots", () => ({
  listTableSnapshots,
  protectTableSnapshot,
  rollbackTableSnapshot,
  listProtectedSnapshots,
  unprotectTableSnapshot: vi.fn()
}));

vi.mock("../../api/client", () => ({
  ApiError: class ApiError extends Error {
    status?: number;
    detail?: unknown;

    constructor(status: number, message: string, detail?: unknown) {
      super(message);
      this.status = status;
      this.detail = detail;
    }
  },
  protectSnapshot: vi.fn()
}));

vi.mock("../../lib/auth", () => ({
  useAuth: () => ({
    api: { token: "test-token" },
    token: "test-token",
    role: authState.role,
    subject: "viewer"
  })
}));

const payload = (): TableSnapshotsResponse => ({
  table_id: "table-1",
  current_snapshot_id: "42",
  total: 2,
  pinned_count: 1,
  latest_committed_at: "2026-09-01T12:00:00Z",
  oldest_committed_at: "2026-08-31T12:00:00Z",
  page: 1,
  page_size: 25,
  snapshots: [
    {
      snapshot_id: "42",
      parent_snapshot_id: "41",
      timestamp_ms: Date.parse("2026-09-01T12:00:00Z"),
      committed_at: "2026-09-01T12:00:00Z",
      sequence_number: 7,
      operation: "append",
      summary: {
        total_records: null,
        total_files_size: null,
        added_data_files: null,
        deleted_data_files: null
      },
      is_current: true,
      refs: [
        {
          name: "main",
          type: "branch",
          snapshot_id: "42",
          min_snapshots_to_keep: 4,
          max_snapshot_age_ms: null,
          max_ref_age_ms: null
        }
      ],
      protected: false,
      protected_label: null
    },
    {
      snapshot_id: "41",
      parent_snapshot_id: null,
      timestamp_ms: Date.parse("2026-08-31T12:00:00Z"),
      committed_at: "2026-08-31T12:00:00Z",
      sequence_number: 6,
      operation: "append",
      summary: {
        total_records: null,
        total_files_size: null,
        added_data_files: null,
        deleted_data_files: null
      },
      is_current: false,
      refs: [],
      protected: false,
      protected_label: null
    }
  ]
});

const renderTab = (path = "/tables/table-1?tab=snapshots") => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/tables/:tableId" element={<SnapshotsTab />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe("SnapshotsTab", () => {
  beforeEach(() => {
    authState.role = "viewer";
    listTableSnapshots.mockReset();
    protectTableSnapshot.mockReset();
    rollbackTableSnapshot.mockReset();
    listProtectedSnapshots.mockReset();
    listProtectedSnapshots.mockResolvedValue({ table_id: "table-1", snapshots: [] });
    listTableSnapshots.mockResolvedValue(payload());
  });

  it("marks the current row, shows unknown size, and disables viewer Protect and rollback", async () => {
    renderTab();

    expect(await screen.findByText("Current")).toBeInTheDocument();
    expect(screen.getAllByText(/Unknown/).length).toBeGreaterThan(0);
    expect(screen.queryByText("0 GB")).not.toBeInTheDocument();
    expect(screen.getAllByText("Excluded from Cryo expiry").length).toBeGreaterThan(0);

    const actionButtons = screen.getAllByRole("button", { name: "Actions" });
    fireEvent.click(actionButtons[0]);

    const protect = screen.getByRole("button", { name: "Protect" });
    expect(protect).toBeDisabled();
    expect(protectTableSnapshot).not.toHaveBeenCalled();

    const currentRowRollback = screen.getByRole("button", { name: "Current snapshot" });
    expect(currentRowRollback).toBeDisabled();

    fireEvent.click(actionButtons[1]);
    const otherRowRollback = screen.getByRole("button", { name: "Apply (roll back)" });
    expect(otherRowRollback).toBeDisabled();
  });

  it("lets an admin roll back to a non-current snapshot after confirming", async () => {
    authState.role = "admin";
    rollbackTableSnapshot.mockResolvedValue({
      table_id: "table-1",
      snapshot_id: "41",
      previous_snapshot_id: "42"
    });
    renderTab();

    await screen.findByText("Current");
    const actionButtons = screen.getAllByRole("button", { name: "Actions" });
    fireEvent.click(actionButtons[1]);

    const rollbackButton = screen.getByRole("button", { name: "Apply (roll back)" });
    expect(rollbackButton).not.toBeDisabled();
    fireEvent.click(rollbackButton);

    const confirmButton = await screen.findByRole("button", { name: "Roll back" });
    fireEvent.click(confirmButton);

    await waitFor(() => expect(rollbackTableSnapshot).toHaveBeenCalledWith(expect.anything(), "table-1", "41"));
  });

  it("reads the page number from the URL", async () => {
    renderTab("/tables/table-1?tab=snapshots&page=2");

    await waitFor(() => {
      expect(listTableSnapshots).toHaveBeenCalledWith({ token: "test-token" }, "table-1", {
        page: 2,
        page_size: 25
      });
    });
  });

  it("copies and compares snapshot ids as exact decimal strings", async () => {
    const big = "9223372036854775807";
    const parent = "9223372036854775806";
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText }
    });
    authState.role = "operator";
    listTableSnapshots.mockResolvedValue({
      ...payload(),
      current_snapshot_id: big,
      total: 2,
      snapshots: [
        {
          ...payload().snapshots[0],
          snapshot_id: big,
          parent_snapshot_id: parent,
          is_current: false,
          refs: [{ ...payload().snapshots[0].refs[0], snapshot_id: big }]
        },
        {
          ...payload().snapshots[0],
          snapshot_id: parent,
          parent_snapshot_id: null,
          is_current: true,
          timestamp_ms: Date.parse("2026-09-01T11:59:59Z"),
          committed_at: "2026-09-01T11:59:59Z",
          refs: []
        }
      ]
    });

    renderTab();

    // Ids are shown shortened; the exact decimal string lives in the data attribute and title.
    await screen.findByRole("button", { name: `Copy snapshot ${big}` });
    const current = document.querySelector(`[data-snapshot-id="${big}"]`) as HTMLElement;
    expect(current).toHaveAttribute("title", big);
    expect(current).toHaveTextContent(big.slice(-8));
    const currentRow = current.closest("tr") as HTMLElement;
    expect(within(currentRow).getByText("Current")).toBeInTheDocument();
    expect(currentRow.querySelector(`[data-parent-snapshot-id="${parent}"]`)).toHaveAttribute("title", parent);

    const adjacent = document.querySelector(`[data-snapshot-id="${parent}"]`) as HTMLElement;
    expect(adjacent).toHaveAttribute("title", parent);
    expect(within(adjacent.closest("tr") as HTMLElement).queryByText("Current")).not.toBeInTheDocument();
    expect(adjacent.closest("tr")).toHaveTextContent("—");

    fireEvent.click(screen.getByRole("button", { name: `Copy snapshot ${big}` }));
    expect(writeText).toHaveBeenCalledWith(big);

    fireEvent.click(within(current.closest("tr") as HTMLElement).getByRole("button", { name: "Actions" }));
    expect(screen.getByRole("button", { name: "Protect" })).toBeEnabled();
  });

  it("shows the writer, row and delete-file change, and partitions per commit", async () => {
    const base = payload();
    listTableSnapshots.mockResolvedValue({
      ...base,
      snapshots: [
        {
          ...base.snapshots[0],
          operation: "overwrite",
          summary: {
            ...base.snapshots[0].summary,
            added_records: 120,
            deleted_records: 40,
            added_delete_files: 3,
            added_position_delete_files: 3,
            changed_partition_count: 2,
            writer: { engine: "trino", engine_version: "480", user: "etl", query_id: "20261018_q1", job_id: null }
          }
        },
        base.snapshots[1]
      ]
    });
    renderTab("/tables/table-1?tab=snapshots&snapshot=42");

    const row = (await screen.findByText("Current")).closest("tr") as HTMLElement;
    expect(within(row).getByText("+120 −40 rows")).toBeInTheDocument();
    expect(within(row).getByText("+3 delete files")).toBeInTheDocument();
    expect(within(row).getByText("2")).toBeInTheDocument();
    expect(within(row).getAllByText("trino 480").length).toBeGreaterThan(0);

    const panel = screen.getByRole("region", { name: "Selected snapshot" });
    expect(within(panel).getByText("3 position")).toBeInTheDocument();
    expect(within(panel).getByText("20261018_q1")).toBeInTheDocument();
    expect(within(panel).getAllByText("Not recorded by this writer").length).toBeGreaterThan(0);
  });
});
