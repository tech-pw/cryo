import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { PresetRead, PresetRuleRead, PresetSimulationResponse } from "../../api/types";
import { PresetDetail } from "./PresetDetail";

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
  listPresets: vi.fn(),
  listPresetRules: vi.fn(),
  simulatePreset: vi.fn(),
  listRuns: vi.fn(),
  listSources: vi.fn(),
  listTables: vi.fn(),
  createPresetRule: vi.fn(),
  updatePreset: vi.fn(),
  deletePresetRule: vi.fn()
}));

import { listPresetRules, listPresets, listRuns, simulatePreset } from "../../api/client";

const preset: PresetRead = {
  id: "11111111-1111-1111-1111-111111111111",
  name: "Nightly compaction",
  destination_id: "dest-1",
  description: "Compacts hot tables nightly",
  mode: "standard",
  operations: ["optimize", "expire_snapshots"],
  params: {},
  schedule_cron: "0 2 * * *",
  priority: 1,
  enabled: true,
  guardrails: {},
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString()
};

const rules: PresetRuleRead[] = [{ id: "rule-1", preset_id: preset.id, predicate: {}, enabled: true }];

const simulation: PresetSimulationResponse = {
  preset_id: preset.id,
  matched_count: 3,
  total_size_gb: 42.5,
  small_files_count: 12,
  sample: []
};

const renderPage = (initialEntries: string[] = [`/operations/presets/${preset.id}`]) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={initialEntries}>
        <Routes>
          <Route path="/operations/presets/:presetId" element={<PresetDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe("PresetDetail overview", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listPresets).mockResolvedValue([preset]);
    vi.mocked(listPresetRules).mockResolvedValue(rules);
    vi.mocked(listRuns).mockResolvedValue([]);
    vi.mocked(simulatePreset).mockResolvedValue(simulation);
  });

  it("shows a human-readable schedule summary with next-run time", async () => {
    renderPage();
    expect(await screen.findByText(/Daily at 02:00 UTC/i)).toBeInTheDocument();
    expect(screen.getByText(/Next run:/i)).toBeInTheDocument();
  });

  it("renders the operation order in canonical maintenance sequence", async () => {
    renderPage();
    const orderLine = await screen.findByText("Operation order:");
    const container = orderLine.parentElement as HTMLElement;
    expect(container.textContent).toMatch(/optimize.*expire snapshots/i);
  });

  it("shows matched-count and size once simulation resolves", async () => {
    renderPage();
    expect(await screen.findByText(/Matched tables: 3/)).toBeInTheDocument();
    expect(screen.getByText(/42.5/)).toBeInTheDocument();
  });

  it("distinguishes a simulation failure from a genuine zero match", async () => {
    vi.mocked(simulatePreset).mockRejectedValue(new Error("simulation timed out"));
    renderPage();
    await waitFor(() => {
      expect(screen.getByText("Simulation unavailable")).toBeInTheDocument();
    });
    expect(screen.getByText(/not the same as zero matches/i)).toBeInTheDocument();
    expect(screen.queryByText(/Matched tables: 0/)).not.toBeInTheDocument();
  });
});
