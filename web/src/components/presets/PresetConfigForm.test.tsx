import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DestinationRead, PresetModeCatalog, PresetRead } from "../../api/types";
import { PresetConfigForm } from "./PresetConfigForm";

vi.mock("../../lib/auth", () => ({
  useAuth: () => ({ api: { token: "t" }, token: "t", role: "admin" })
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

vi.mock("../../api/client", () => ({
  listDestinations: vi.fn(),
  getPresetModes: vi.fn(),
  patchPreset: vi.fn(),
  createPreset: vi.fn(),
  simulatePreset: vi.fn()
}));

import { getPresetModes, listDestinations, patchPreset, simulatePreset } from "../../api/client";

const catalog: PresetModeCatalog = {
  modes: [
    { mode: "light", description: "Expires old snapshots and data only.", operations: ["expire_data", "expire_snapshots"], recommended: false },
    {
      mode: "standard",
      description: "Compacts small files, then expires snapshots.",
      operations: ["optimize", "expire_snapshots"],
      recommended: false
    },
    {
      mode: "deep",
      description: "Standard plus manifest rewrite and orphan-file cleanup.",
      operations: ["optimize", "optimize_manifests", "expire_snapshots", "remove_orphan_files"],
      recommended: true
    },
    { mode: "manifest_only", description: "Rewrites manifests only.", operations: ["optimize_manifests"], recommended: false }
  ],
  param_defaults: [
    { key: "orphan_cadence_days", value: 7, source: "deployment", setting: "orphan_removal_cadence_days" },
    { key: "orphan_catchup_min_bytes", value: 10 * 1024 ** 3, source: "deployment", setting: "orphan_catchup_min_bytes" },
    { key: "orphan_max_listing_keys", value: 5_000_000, source: "deployment", setting: "orphan_max_listing_keys" },
    { key: "min_manifests", value: 100, source: "built_in", setting: null }
  ]
};

const destination = {
  id: "dest-1",
  name: "spark-main",
  engine: "spark_emr",
  config: {},
  enabled: true
} as unknown as DestinationRead;

const preset: PresetRead = {
  id: "11111111-1111-1111-1111-111111111111",
  name: "default-compact",
  destination_id: "dest-1",
  description: "Compact on Spark",
  mode: "standard",
  // Stale list from before operations followed the mode.
  operations: ["optimize"],
  params: {},
  schedule_cron: "0 2 * * *",
  priority: 100,
  enabled: true,
  is_default: false,
  paused: false,
  guardrails: {},
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z"
};

const renderForm = (p: PresetRead = preset) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/preset"]}>
        <Routes>
          <Route
            path="/preset"
            element={
              <>
                <Link to="/elsewhere">Go elsewhere</Link>
                <PresetConfigForm preset={p} onSaved={vi.fn()} />
              </>
            }
          />
          <Route path="/elsewhere" element={<p>Elsewhere page</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
};

const modeRadio = (name: RegExp) => screen.getByRole("radio", { name });

describe("PresetConfigForm", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listDestinations).mockResolvedValue([destination]);
    vi.mocked(getPresetModes).mockResolvedValue(catalog);
    vi.mocked(patchPreset).mockImplementation(async (_api, _id, body) => ({ ...preset, ...body }) as PresetRead);
    vi.mocked(simulatePreset).mockResolvedValue({
      preset_id: preset.id,
      matched_count: 42,
      total_size_gb: 10,
      small_files_count: 0,
      sample: [],
      orphan_backlog_bytes: 3 * 1024 ** 3,
      orphan_backlog_tables: 5
    });
  });

  it("labels the recommended mode and describes each option", async () => {
    renderForm();
    const deep = await waitFor(() => modeRadio(/^Deep/));
    const card = deep.closest("label") as HTMLElement;
    expect(within(card).getByText("Recommended")).toBeInTheDocument();
    expect(within(card).getByText(/orphan-file cleanup/)).toBeInTheDocument();
    expect(screen.queryByText(/\(default\)/)).not.toBeInTheDocument();
    expect(screen.getAllByRole("radio")).toHaveLength(4);
  });

  it("shows the operations the mode runs, read-only, and no operation checkboxes", async () => {
    renderForm();
    const ops = await screen.findByTestId("mode-operations");
    await waitFor(() => expect(ops).toHaveTextContent("Standard runs:"));
    expect(ops).toHaveTextContent("optimize");
    expect(ops).not.toHaveTextContent("remove orphan files");
    expect(screen.queryByRole("checkbox", { name: /optimize/ })).not.toBeInTheDocument();

    fireEvent.click(modeRadio(/^Deep/));
    expect(ops).toHaveTextContent("Deep runs:");
    expect(ops).toHaveTextContent("remove orphan files");
  });

  it("shows the sticky save bar only when dirty and discards back to the saved values", async () => {
    renderForm();
    await waitFor(() => modeRadio(/^Standard/));
    expect(screen.queryByRole("region", { name: "Unsaved changes" })).not.toBeInTheDocument();

    fireEvent.click(modeRadio(/^Deep/));
    const bar = await screen.findByRole("region", { name: "Unsaved changes" });
    expect(within(bar).getByRole("button", { name: "Save preset" })).toBeInTheDocument();

    fireEvent.click(within(bar).getByRole("button", { name: "Discard" }));
    await waitFor(() => expect(screen.queryByRole("region", { name: "Unsaved changes" })).not.toBeInTheDocument());
    expect(modeRadio(/^Standard/)).toBeChecked();
  });

  it("won't save until the mode catalog has loaded, so operations always match the mode", async () => {
    vi.mocked(getPresetModes).mockReturnValue(new Promise(() => {}));
    renderForm();
    const name = await screen.findByDisplayValue(preset.name);
    fireEvent.change(name, { target: { value: `${preset.name} edited` } });
    const bar = await screen.findByRole("region", { name: "Unsaved changes" });
    expect(within(bar).getByRole("button", { name: "Save preset" })).toBeDisabled();
    expect(patchPreset).not.toHaveBeenCalled();
  });

  it("hints to update the description after a mode change", async () => {
    renderForm();
    await waitFor(() => modeRadio(/^Deep/));
    fireEvent.click(modeRadio(/^Deep/));
    expect(await screen.findByText(/Update the description if it still describes the old mode/)).toBeInTheDocument();
  });

  it("guards internal navigation while dirty", async () => {
    renderForm();
    await waitFor(() => modeRadio(/^Deep/));
    fireEvent.click(modeRadio(/^Deep/));
    await screen.findByRole("region", { name: "Unsaved changes" });

    fireEvent.click(screen.getByRole("link", { name: "Go elsewhere" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText("Discard unsaved changes?")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Keep editing" }));
    expect(screen.queryByText("Elsewhere page")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "Go elsewhere" }));
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Discard changes" }));
    expect(await screen.findByText("Elsewhere page")).toBeInTheDocument();
  });

  it("registers a beforeunload prompt only while dirty", async () => {
    const add = vi.spyOn(window, "addEventListener");
    renderForm();
    await waitFor(() => modeRadio(/^Deep/));
    expect(add.mock.calls.some(([type]) => type === "beforeunload")).toBe(false);
    fireEvent.click(modeRadio(/^Deep/));
    await waitFor(() => expect(add.mock.calls.some(([type]) => type === "beforeunload")).toBe(true));
    add.mockRestore();
  });

  it("confirms a mode change with tables covered, operations added and orphan backlog", async () => {
    renderForm();
    await waitFor(() => modeRadio(/^Deep/));
    fireEvent.click(modeRadio(/^Deep/));
    fireEvent.click(await screen.findByRole("button", { name: "Save preset" }));

    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText("Switch to Deep mode?")).toBeInTheDocument();
    const review = within(dialog).getByTestId("save-review");
    expect(review).toHaveTextContent("42 tables");
    expect(review).toHaveTextContent("Adds: optimize manifests, remove orphan files");
    expect(review).toHaveTextContent("across 5 tables");
    expect(patchPreset).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole("button", { name: "Save preset" }));
    await waitFor(() => expect(patchPreset).toHaveBeenCalledTimes(1));
    const body = vi.mocked(patchPreset).mock.calls[0][2];
    expect(body.mode).toBe("deep");
    // operations follows the mode for older readers of the field.
    expect(body.operations).toEqual(catalog.modes[2].operations);
  });

  it("round-trips the deep-mode thresholds with their effective value and source", async () => {
    renderForm({ ...preset, mode: "deep", params: { orphan_cadence_days: 3, min_manifests: 40 } });
    expect(await screen.findByTestId("effective-orphan_cadence_days")).toHaveTextContent(
      "Effective: every 3 days · set on this preset"
    );
    expect(screen.getByTestId("effective-orphan_catchup_min_gb")).toHaveTextContent("10 GiB · deployment default");
    expect(screen.getByTestId("effective-min_manifests")).toHaveTextContent("40 manifests · set on this preset");

    const catchup = screen.getByRole("spinbutton", { name: /Catch-up threshold/ });
    fireEvent.change(catchup, { target: { value: "2" } });
    const cadence = screen.getByRole("spinbutton", { name: /Orphan cleanup every/ });
    fireEvent.change(cadence, { target: { value: "" } });
    expect(screen.getByTestId("effective-orphan_cadence_days")).toHaveTextContent("every 7 days · deployment default");

    fireEvent.click(await screen.findByRole("button", { name: "Save preset" }));
    await waitFor(() => expect(patchPreset).toHaveBeenCalledTimes(1));
    const params = vi.mocked(patchPreset).mock.calls[0][2].params as Record<string, unknown>;
    expect(params.orphan_catchup_min_bytes).toBe(2 * 1024 ** 3);
    expect(params.min_manifests).toBe(40);
    expect("orphan_cadence_days" in params).toBe(false);
  });
});
