import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DestinationRead } from "../../api/types";
import { EnginesTab } from "./EnginesTab";

const apiFetch = vi.hoisted(() => vi.fn());
const destinations = vi.hoisted(() => ({ data: [] as DestinationRead[] }));

vi.mock("../../lib/auth", () => ({
  useAuth: () => ({
    api: { token: "test-token" },
    token: "test-token",
    role: "admin",
    subject: "tester"
  })
}));

vi.mock("../../api/client", () => ({
  apiFetch,
  patchDestination: vi.fn()
}));

vi.mock("./ConnectionsContext", () => ({
  useConnectionsContext: () => ({
    api: { token: "test-token" },
    destinations: { data: destinations.data, isLoading: false },
    pendingScanVersion: 0,
    setDestinationDrawer: vi.fn(),
    probe: { isPending: false, mutate: vi.fn() },
    purgeDestinationMutation: { isPending: false, mutate: vi.fn() },
    queryClient: { invalidateQueries: vi.fn() }
  })
}));

const nativeDestination: DestinationRead = {
  id: "dest-native",
  name: "iceberg-go",
  engine: "native",
  config: { engine_impl: "go" },
  enabled: true,
  created_at: "",
  updated_at: ""
};

const renderTab = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <EnginesTab />
    </QueryClientProvider>
  );
};

describe("EnginesTab native fleet flag", () => {
  beforeEach(() => {
    destinations.data = [nativeDestination];
    apiFetch.mockReset();
  });

  it("labels a native engine disabled fleet-wide when the runtime flag is false", async () => {
    apiFetch.mockResolvedValue({
      native_engine_enabled: false,
      maintenance_writes_enabled: true,
      native_engine_fallback: "the configured Spark SQL fallback"
    });

    renderTab();

    const status = await screen.findByText(
      "Disabled fleet-wide — work runs on the configured Spark SQL fallback"
    );
    const row = status.closest("tr");
    expect(row).not.toBeNull();
    expect(
      within(row as HTMLElement).getByText("Native (iceberg-go) (disabled fleet-wide)")
    ).toBeInTheDocument();
    expect(within(row as HTMLElement).queryByText("Enabled")).not.toBeInTheDocument();
    expect(apiFetch).toHaveBeenCalledWith("/runtime", { token: "test-token" });
  });

  it("keeps the destination enabled badge when native is on and adds no fleet control", async () => {
    apiFetch.mockResolvedValue({
      native_engine_enabled: true,
      maintenance_writes_enabled: true,
      native_engine_fallback: "the configured Spark SQL fallback"
    });

    renderTab();

    const name = await screen.findByText("iceberg-go");
    const row = name.closest("tr");
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getByText("Native (iceberg-go)")).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText("Enabled")).toBeInTheDocument();
    expect(screen.queryByText(/Disabled fleet-wide/)).not.toBeInTheDocument();
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  });
});
