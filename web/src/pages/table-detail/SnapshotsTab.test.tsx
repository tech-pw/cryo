import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { TableSnapshotsResponse } from "../../api/snapshots";
import { SnapshotsTab } from "./SnapshotsTab";

const { listTableSnapshots, protectTableSnapshot, listProtectedSnapshots, authState } = vi.hoisted(() => ({
  listTableSnapshots: vi.fn(),
  protectTableSnapshot: vi.fn(),
  listProtectedSnapshots: vi.fn(),
  authState: { role: "viewer" }
}));

vi.mock("../../api/snapshots", () => ({
  listTableSnapshots,
  protectTableSnapshot,
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
  total: 1,
  pinned_count: 1,
  latest_committed_at: "2026-09-01T12:00:00Z",
  oldest_committed_at: "2026-09-01T12:00:00Z",
  page: 1,
  page_size: 25,
  snapshots: [
    {
      snapshot_id: "42",
      parent_snapshot_id: null,
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
    listProtectedSnapshots.mockReset();
    listProtectedSnapshots.mockResolvedValue({ table_id: "table-1", snapshots: [] });
    listTableSnapshots.mockResolvedValue(payload());
  });

  it("marks the current row, shows unknown size, and disables Restore and viewer Protect", async () => {
    renderTab();

    expect(await screen.findByText("Current")).toBeInTheDocument();
    expect(screen.getByText("Unknown")).toBeInTheDocument();
    expect(screen.queryByText("0 GB")).not.toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Actions" }));

    expect(screen.queryByRole("button", { name: "Restore…" })).not.toBeInTheDocument();
    expect(screen.getByText("Restore is not enabled on this install.")).toBeInTheDocument();

    const protect = screen.getByRole("button", { name: "Protect" });
    expect(protect).toBeDisabled();
    expect(protectTableSnapshot).not.toHaveBeenCalled();
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
});
