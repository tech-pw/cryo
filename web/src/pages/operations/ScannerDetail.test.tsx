import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CryoTooltipProvider } from "../../components/ui/tooltip";
import { ScannerDetail } from "./ScannerDetail";
import type { ScannerConfigRead, ScannerStatusRead } from "../../api/types";

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
  getScannerConfig: vi.fn(),
  getScannerStatus: vi.fn(),
  getSourceNamespaces: vi.fn(),
  getSourceNamespaceTables: vi.fn(),
  listRuns: vi.fn(),
  listSources: vi.fn(),
  listTables: vi.fn(),
  patchScannerConfig: vi.fn(),
  triggerScan: vi.fn()
}));

import {
  getScannerConfig,
  getScannerStatus,
  listRuns,
  listSources
} from "../../api/client";

const scannerConfig: ScannerConfigRead = {
  id: 1,
  enabled: true,
  schedule_cron: "0 2 * * *",
  timezone: "UTC",
  num_chunks: 8,
  full_scan_default: false,
  cost_full_scan_default: false,
  max_concurrent_runs: 1,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString()
};

const scannerStatus: ScannerStatusRead = {
  config: scannerConfig,
  queue_depth: 0,
  latest_run: null,
  active_run: null,
  last_successful_run: null,
  next_scan_at: "2026-02-01T02:00:00Z",
  total_tables: 5,
  tables_with_stats: 5,
  latest_table_scan_at: null
};

const renderPage = (initialEntries: string[] = ["/operations/scanner"]) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <CryoTooltipProvider>
        <MemoryRouter initialEntries={initialEntries}>
          <Routes>
            <Route path="/operations/scanner" element={<ScannerDetail />} />
          </Routes>
        </MemoryRouter>
      </CryoTooltipProvider>
    </QueryClientProvider>
  );
};

describe("ScannerDetail", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getScannerConfig).mockResolvedValue(scannerConfig);
    vi.mocked(getScannerStatus).mockResolvedValue(scannerStatus);
    vi.mocked(listRuns).mockResolvedValue([]);
    vi.mocked(listSources).mockResolvedValue([]);
  });

  it("restores the Config tab from the URL", async () => {
    renderPage(["/operations/scanner?tab=config"]);
    await waitFor(() => {
      expect(screen.getByRole("tab", { name: "Config", selected: true })).toBeInTheDocument();
    });
    expect(await screen.findByText("Trigger a scan")).toBeInTheDocument();
  });

  it("persists tab switches to the URL", async () => {
    renderPage();
    const configTab = await screen.findByRole("tab", { name: "Config" });
    fireEvent.mouseDown(configTab, { button: 0 });
    await waitFor(() => {
      expect(screen.getByRole("tab", { name: "Config", selected: true })).toBeInTheDocument();
    });
  });

  it("shows a human-readable schedule summary and next run beside the cron input", async () => {
    renderPage(["/operations/scanner?tab=config"]);
    expect(await screen.findByText(/Daily at 02:00 UTC/i)).toBeInTheDocument();
  });

  it("describes the full scan scope before confirming", async () => {
    renderPage(["/operations/scanner?tab=config"]);
    const triggerButton = await screen.findByRole("button", { name: "Trigger scan" });
    fireEvent.click(triggerButton);
    await waitFor(() => {
      expect(screen.getByText(/entire catalog/i)).toBeInTheDocument();
    });
  });
});
