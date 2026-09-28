import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { TableSnapshotsResponse } from "../../api/snapshots";
import { VIEWER_REASON } from "../../lib/permissions";
import { SnapshotsTab } from "./SnapshotsTab";

const { listTableSnapshots, protectSnapshot } = vi.hoisted(() => ({
  listTableSnapshots: vi.fn(),
  protectSnapshot: vi.fn()
}));

vi.mock("../../api/snapshots", () => ({
  listTableSnapshots
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
  protectSnapshot
}));

vi.mock("../../lib/auth", () => ({
  useAuth: () => ({
    api: { token: "test-token" },
    token: "test-token",
    role: "viewer",
    subject: "viewer"
  })
}));

const payload = (): TableSnapshotsResponse => ({
  table_id: "table-1",
  current_snapshot_id: 42,
  total: 1,
  pinned_count: 1,
  latest_committed_at: "2026-09-01T12:00:00Z",
  oldest_committed_at: "2026-09-01T12:00:00Z",
  page: 1,
  page_size: 25,
  snapshots: [
    {
      snapshot_id: 42,
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
    listTableSnapshots.mockReset();
    protectSnapshot.mockReset();
    listTableSnapshots.mockResolvedValue(payload());
  });

  it("marks the current row, shows unknown size, and disables Restore and viewer Protect", async () => {
    renderTab();

    expect(await screen.findByText("Current")).toBeInTheDocument();
    expect(screen.getByText("Unknown")).toBeInTheDocument();
    expect(screen.queryByText("0 GB")).not.toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Actions" }));

    const restore = screen.getByRole("menuitem", { name: "Restore…" });
    expect(restore).toBeDisabled();
    expect(screen.getByText("Restore is not enabled on this install")).toBeInTheDocument();

    const protect = screen.getByRole("menuitem", { name: "Protect" });
    expect(protect).toBeDisabled();
    expect(screen.getByText(VIEWER_REASON)).toBeInTheDocument();
    expect(protectSnapshot).not.toHaveBeenCalled();
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
});
