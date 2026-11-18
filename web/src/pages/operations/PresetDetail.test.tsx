import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
  deletePresetRule: vi.fn(),
  listDestinations: vi.fn(),
  getPresetModes: vi.fn(),
  patchPreset: vi.fn(),
  createPreset: vi.fn()
}));

import { getPresetModes, listDestinations, listPresetRules, listPresets, listRuns, simulatePreset } from "../../api/client";

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

  it("lists what the mode runs, not a stale stored operations list", async () => {
    vi.mocked(listPresets).mockResolvedValue([{ ...preset, mode: "deep", operations: ["optimize"] }]);
    vi.mocked(getPresetModes).mockResolvedValue({
      modes: [
        {
          mode: "deep",
          description: "Plus orphans.",
          operations: ["optimize", "expire_snapshots", "remove_orphan_files"],
          recommended: true
        }
      ],
      param_defaults: []
    });
    renderPage();
    const orderLine = await screen.findByText("Operation order:");
    await waitFor(() =>
      expect((orderLine.parentElement as HTMLElement).textContent).toMatch(/remove orphan files/i)
    );
  });

  it("shows matched-count and size once simulation resolves", async () => {
    renderPage();
    expect(await screen.findByText(/Matched tables: 3/)).toBeInTheDocument();
    expect(screen.getByText(/45.6 GB/)).toBeInTheDocument();
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

describe("PresetDetail config tab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listPresets).mockResolvedValue([preset]);
    vi.mocked(listPresetRules).mockResolvedValue(rules);
    vi.mocked(listRuns).mockResolvedValue([]);
    vi.mocked(simulatePreset).mockResolvedValue(simulation);
    vi.mocked(listDestinations).mockResolvedValue([
      { id: "dest-1", name: "spark-main", engine: "spark_emr", config: {}, enabled: true } as never
    ]);
    vi.mocked(getPresetModes).mockResolvedValue({
      modes: [
        { mode: "standard", description: "Compacts.", operations: ["optimize", "expire_snapshots"], recommended: false },
        {
          mode: "deep",
          description: "Plus orphans.",
          operations: ["optimize", "expire_snapshots", "remove_orphan_files"],
          recommended: true
        }
      ],
      param_defaults: []
    });
  });

  it("keeps assignment rules in their own section outside the form", async () => {
    renderPage([`/operations/presets/${preset.id}?tab=config`]);
    const heading = await screen.findByText("Assignment rules");
    const form = await screen.findByRole("form", { name: "Preset configuration" });
    expect(form.contains(heading)).toBe(false);
  });

  it("asks before leaving the Config tab with unsaved changes", async () => {
    renderPage([`/operations/presets/${preset.id}?tab=config`]);
    fireEvent.click(await screen.findByRole("radio", { name: /^Deep/ }));
    await screen.findByRole("region", { name: "Unsaved changes" });

    const runsTab = screen.getByRole("tab", { name: "Runs" });
    fireEvent.mouseDown(runsTab);
    fireEvent.click(runsTab);
    const dialog = await screen.findByRole("alertdialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Keep editing" }));
    expect(screen.getByRole("tab", { name: "Config" })).toHaveAttribute("data-state", "active");

    fireEvent.mouseDown(runsTab);
    fireEvent.click(runsTab);
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Discard changes" }));
    await waitFor(() => expect(screen.getByRole("tab", { name: "Runs" })).toHaveAttribute("data-state", "active"));
  });
});
