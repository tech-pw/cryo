import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CryoTooltipProvider } from "../../components/ui/tooltip";
import { OperationsHome } from "./OperationsHome";
import type { PresetRead, RunRead, ScannerStatusRead } from "../../api/types";

vi.mock("../../lib/auth", () => ({
  useAuth: () => ({
    api: { token: "test-token" },
    token: "test-token",
    role: "admin",
    subject: "tester",
    loading: false,
    error: null,
    isAuthenticated: true,
    signIn: vi.fn(),
    signOut: vi.fn()
  })
}));

vi.mock("../../api/client", () => ({
  getScannerStatus: vi.fn(),
  listPresets: vi.fn(),
  listRuns: vi.fn()
}));

import { getScannerStatus, listPresets, listRuns } from "../../api/client";

const scannerStatus: ScannerStatusRead = {
  config: { schedule_cron: "0 2 * * *", enabled: true } as ScannerStatusRead["config"],
  queue_depth: 0,
  latest_run: null,
  active_run: null,
  last_successful_run: null,
  next_scan_at: null,
  total_tables: 10,
  tables_with_stats: 10,
  latest_table_scan_at: null
};

const preset: PresetRead = {
  id: "preset-1",
  name: "Nightly compaction",
  enabled: true,
  schedule_cron: "0 3 * * *"
} as PresetRead;

const renderPage = (initialEntries: string[] = ["/operations"]) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <CryoTooltipProvider>
        <MemoryRouter initialEntries={initialEntries}>
          <Routes>
            <Route path="/operations" element={<OperationsHome />} />
          </Routes>
        </MemoryRouter>
      </CryoTooltipProvider>
    </QueryClientProvider>
  );
};

describe("OperationsHome", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows a 'Not run' badge instead of a fake queued status for workflows with no runs", async () => {
    vi.mocked(getScannerStatus).mockResolvedValue(scannerStatus);
    vi.mocked(listPresets).mockResolvedValue([preset]);
    vi.mocked(listRuns).mockResolvedValue([]);

    renderPage();

    expect(await screen.findAllByText("Not run")).not.toHaveLength(0);
    expect(screen.queryByText("Queued")).not.toBeInTheDocument();
  });

  it("explains why 'New preset' is disabled via an accessible tooltip", async () => {
    vi.mocked(getScannerStatus).mockResolvedValue(scannerStatus);
    vi.mocked(listPresets).mockResolvedValue([]);
    vi.mocked(listRuns).mockResolvedValue([]);

    renderPage();

    const trigger = screen.getByText("New preset").closest("span");
    expect(trigger).toBeTruthy();
    fireEvent.focus(trigger as HTMLElement);
    await waitFor(() => {
      expect(screen.getByText(/preset creation isn't available/i)).toBeInTheDocument();
    });
  });

  it("switching to the runs tab persists the tab selection in the URL", async () => {
    vi.mocked(getScannerStatus).mockResolvedValue(scannerStatus);
    vi.mocked(listPresets).mockResolvedValue([preset]);
    vi.mocked(listRuns).mockResolvedValue([
      { id: "run-1", kind: "scan", status: "failed", created_at: new Date().toISOString() } as RunRead
    ]);

    renderPage();

    const failedQuickLink = await screen.findByRole("button", { name: /failed runs/i });
    fireEvent.click(failedQuickLink);

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: "All runs", selected: true })).toBeInTheDocument();
    });
    // The "Failed" filter button within the runs tab should now be the active one.
    const failedFilterButton = screen.getByRole("button", { name: "Failed" });
    expect(failedFilterButton.className).toMatch(/bg-primary/);
  });

  it("restores the tab and run filter from the URL on load", async () => {
    vi.mocked(getScannerStatus).mockResolvedValue(scannerStatus);
    vi.mocked(listPresets).mockResolvedValue([preset]);
    vi.mocked(listRuns).mockResolvedValue([
      { id: "run-1", kind: "scan", status: "failed", created_at: new Date().toISOString() } as RunRead
    ]);

    renderPage(["/operations?tab=runs&runFilter=failed"]);

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: "All runs", selected: true })).toBeInTheDocument();
    });
    const failedFilterButton = screen.getByRole("button", { name: "Failed" });
    expect(failedFilterButton.className).toMatch(/bg-primary/);
  });
});
