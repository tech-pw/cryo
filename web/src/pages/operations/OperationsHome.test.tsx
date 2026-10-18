import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
  listPendingApprovals: vi.fn(),
  listPresets: vi.fn(),
  listRuns: vi.fn(),
  apiFetch: vi.fn()
}));

vi.mock("../../api/engineLoad", () => ({
  getEngineLoad: vi.fn()
}));

import { getScannerStatus, listPendingApprovals, listPresets, listRuns } from "../../api/client";
import { getEngineLoad } from "../../api/engineLoad";

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
    vi.mocked(listPendingApprovals).mockResolvedValue([]);
    vi.mocked(getEngineLoad).mockResolvedValue({ engines: [] });
  });

  it("shows a 'Not run' badge instead of a fake queued status for workflows with no runs", async () => {
    vi.mocked(getScannerStatus).mockResolvedValue(scannerStatus);
    vi.mocked(listPresets).mockResolvedValue([preset]);
    vi.mocked(listRuns).mockResolvedValue([]);

    renderPage();

    expect(await screen.findAllByText("Not run")).not.toHaveLength(0);
    expect(screen.queryByText("Queued")).not.toBeInTheDocument();
  });

  it("shows the Nightly maintenance row from batch runs and no dead preset control", async () => {
    const older: RunRead = {
      id: "aaaaaaaa-0000-0000-0000-000000000001",
      kind: "scheduled",
      status: "failed",
      trigger: "batch-cron",
      created_at: "2026-09-26T02:00:00Z",
      summary: { pipeline: "batch" }
    };
    const newer: RunRead = {
      id: "bbbbbbbb-0000-0000-0000-000000000002",
      kind: "scheduled",
      status: "partial",
      trigger: "batch-cron",
      created_at: "2026-09-27T02:00:00Z",
      summary: { pipeline: "batch" }
    };
    const notBatch: RunRead = {
      id: "cccccccc-0000-0000-0000-000000000003",
      kind: "adhoc",
      status: "succeeded",
      created_at: "2026-09-28T02:00:00Z",
      summary: { pipeline: "other" }
    };

    vi.mocked(getScannerStatus).mockResolvedValue(scannerStatus);
    vi.mocked(listPresets).mockResolvedValue([]);
    vi.mocked(listRuns).mockImplementation(async (_api, args) => (args?.kind === "scan" ? [] : [older, notBatch, newer]));

    renderPage();

    const nightlyLink = await screen.findByRole("link", { name: "Nightly maintenance" });
    const row = nightlyLink.closest("tr");
    expect(row).toBeTruthy();
    const nightlyRow = within(row as HTMLElement);

    expect(nightlyLink).toHaveAttribute("href", "/runs/bbbbbbbb-0000-0000-0000-000000000002");
    // The batch CronJob's time is only in Helm values and not exposed by any API, so the
    // row names where it is configured rather than implying Cryo knows the schedule.
    expect(nightlyRow.getByText("Kubernetes CronJob (set in Helm)")).toBeInTheDocument();
    expect(nightlyRow.getByText("Partial")).toBeInTheDocument();
    expect(nightlyRow.getByRole("list", { name: /last 2 runs/i })).toBeInTheDocument();
    expect(nightlyRow.getByRole("button", { name: "Nightly maintenance actions" })).toBeInTheDocument();
    expect(screen.getByText("Maintenance is driven by the nightly batch.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /new preset/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/new preset/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/nothing configured/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/preset creation isn't available/i)).not.toBeInTheDocument();
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
      expect(screen.getByRole("tab", { name: "History", selected: true })).toBeInTheDocument();
    });
    // The "Failed" filter button within the runs tab should now be the active one.
    const failedFilterButton = screen.getByRole("button", { name: "Failed · 24h" });
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
      expect(screen.getByRole("tab", { name: "History", selected: true })).toBeInTheDocument();
    });
    const failedFilterButton = screen.getByRole("button", { name: "Failed · 24h" });
    expect(failedFilterButton.className).toMatch(/bg-primary/);
  });
});
