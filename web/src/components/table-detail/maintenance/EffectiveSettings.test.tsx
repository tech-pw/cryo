import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { EffectiveSettings } from "./EffectiveSettings";

const client = vi.hoisted(() => ({
  getEffectiveParams: vi.fn(),
  getEffectivePreset: vi.fn(),
  listPresets: vi.fn().mockResolvedValue([]),
  listDestinations: vi.fn().mockResolvedValue([]),
  listCompatiblePresets: vi.fn().mockResolvedValue([]),
  assignTables: vi.fn().mockResolvedValue({ affected: 1 }),
  unassignTables: vi.fn().mockResolvedValue({ affected: 1 }),
  patchTableParamOverrides: vi.fn()
}));

const auth = vi.hoisted(() => ({ role: "viewer" as "viewer" | "operator" | "admin" }));
const writeAccess = vi.hoisted(() => ({ allowed: false, reason: "viewer" as string | null, hidden: false }));

vi.mock("../../../api/client", () => client);
vi.mock("../../../lib/auth", () => ({
  useAuth: () => ({
    api: { token: "test-token" },
    token: "test-token",
    role: auth.role
  })
}));
vi.mock("../../../lib/useWriteControl", () => ({
  useWriteControl: () => writeAccess
}));

const renderSettings = () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <EffectiveSettings tableId="table-1" />
    </QueryClientProvider>
  );
};

describe("EffectiveSettings", () => {
  beforeEach(() => {
    auth.role = "viewer";
    writeAccess.allowed = false;
    writeAccess.reason = "viewer";
    client.getEffectivePreset.mockResolvedValue({
      table_id: "table-1",
      preset_id: "preset-1",
      preset_name: "Nightly",
      provenance: "assignment"
    });
    client.getEffectiveParams.mockResolvedValue({
      table_id: "table-1",
      preset_id: "preset-1",
      preset_name: "Nightly",
      preset_params: { retain_last: 5 },
      param_overrides: {},
      effective_params: { retain_last: 5 }
    });
    client.listPresets.mockClear();
    client.listDestinations.mockClear();
    client.listCompatiblePresets.mockClear();
  });

  it("shows where each param comes from and how the preset was chosen", async () => {
    renderSettings();
    expect(await screen.findByText("retain_last")).toBeInTheDocument();
    expect(screen.getByText("Preset")).toBeInTheDocument();
    expect(screen.getByText("Assigned to this table by an operator.")).toBeInTheDocument();
    expect(screen.queryByText("engine default")).not.toBeInTheDocument();
    expect(document.getElementById("maintenance-settings")).toBeTruthy();
  });

  it("loads presets and destinations only after the picker opens", async () => {
    writeAccess.allowed = true;
    writeAccess.reason = null;
    client.listCompatiblePresets.mockResolvedValue([{ id: "preset-2", name: "Hot", destination_id: "dest-1" }]);
    client.listDestinations.mockResolvedValue([{ id: "dest-1", name: "Trino", engine: "trino" }]);
    renderSettings();

    expect(await screen.findByRole("button", { name: "Change workflow" })).toBeEnabled();
    expect(client.listPresets).not.toHaveBeenCalled();
    expect(client.listDestinations).not.toHaveBeenCalled();
    expect(client.listCompatiblePresets).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Change workflow" }));
    await waitFor(() => {
      expect(client.listPresets).toHaveBeenCalled();
      expect(client.listDestinations).toHaveBeenCalled();
      expect(client.listCompatiblePresets).toHaveBeenCalledWith({ token: "test-token" }, "table-1");
    });
  });

  it("keeps parameter overrides admin-only", async () => {
    renderSettings();
    const add = await screen.findByRole("button", { name: "Add override" });
    expect(add).toBeDisabled();
    fireEvent.click(add);
    expect(client.patchTableParamOverrides).not.toHaveBeenCalled();

    auth.role = "admin";
    renderSettings();
    fireEvent.change(screen.getAllByLabelText("Override name")[1], { target: { value: "retain_last" } });
    fireEvent.change(screen.getAllByLabelText("Override value")[1], { target: { value: "2" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Add override" })[1]);
    await waitFor(() => {
      expect(client.patchTableParamOverrides).toHaveBeenCalledWith({ token: "test-token" }, "table-1", {
        retain_last: 2
      });
    });
  });
});
