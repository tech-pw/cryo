import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DestinationRead } from "../../api/types";
import { EnginesTab } from "./EnginesTab";

const apiFetch = vi.hoisted(() => vi.fn());
const destinations = vi.hoisted(() => ({ data: [] as DestinationRead[] }));
const setDestinationDrawer = vi.hoisted(() => vi.fn());
const listPresets = vi.hoisted(() => vi.fn());

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
  listPresets,
  patchDestination: vi.fn()
}));

vi.mock("./ConnectionsContext", () => ({
  useConnectionsContext: () => ({
    api: { token: "test-token" },
    destinations: { data: destinations.data, isLoading: false },
    pendingScanVersion: 0,
    setDestinationDrawer,
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
    listPresets.mockResolvedValue([]);
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
    const row = status.closest("li");
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
    const row = name.closest("li");
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getByText("Native (iceberg-go)")).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText("Enabled")).toBeInTheDocument();
    expect(screen.queryByText(/Disabled fleet-wide/)).not.toBeInTheDocument();
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  });
});

describe("EnginesTab grouping", () => {
  const trino: DestinationRead = {
    id: "dest-trino",
    name: "trino-prod",
    engine: "trino",
    config: { coordinators: [{ host: "trino", port: 8080 }] },
    enabled: true,
    created_at: "",
    updated_at: ""
  };
  const sparkK8s: DestinationRead = {
    id: "dest-spark",
    name: "spark-k8s",
    engine: "spark_emr",
    config: { submit_mode: "k8s" },
    enabled: true,
    created_at: "",
    updated_at: ""
  };

  beforeEach(() => {
    destinations.data = [trino, nativeDestination, sparkK8s];
    apiFetch.mockReset();
    apiFetch.mockResolvedValue({ native_engine_enabled: true, maintenance_writes_enabled: true });
    listPresets.mockResolvedValue([{ id: "p1", is_default: true, enabled: true, destination_id: "dest-spark" }]);
    setDestinationDrawer.mockReset();
  });

  it("lists Cryo-run engines apart from the engines an operator added", async () => {
    renderTab();
    const cryo = screen.getByRole("list", { name: "Cryo engines" });
    const yours = screen.getByRole("list", { name: "Your engines" });
    expect(within(cryo).getByText("iceberg-go")).toBeInTheDocument();
    expect(within(cryo).getByText("spark-k8s")).toBeInTheDocument();
    expect(within(yours).getByText("trino-prod")).toBeInTheDocument();
    expect(within(yours).queryByText("spark-k8s")).not.toBeInTheDocument();
    const sparkRow = within(cryo).getByText("spark-k8s").closest("li") as HTMLElement;
    expect(await within(sparkRow).findByText("Default preset")).toBeInTheDocument();
  });

  it("confirms the caveat before editing a Cryo engine", async () => {
    renderTab();
    const cryo = screen.getByRole("list", { name: "Cryo engines" });
    const sparkRow = within(cryo).getByText("spark-k8s").closest("li") as HTMLElement;
    fireEvent.click(within(sparkRow).getByRole("button", { name: "Edit" }));
    expect(setDestinationDrawer).not.toHaveBeenCalled();
    expect(await screen.findByText(/exactly one enabled Spark engine/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Edit anyway" }));
    await vi.waitFor(() => expect(setDestinationDrawer).toHaveBeenCalledWith(sparkK8s));
  });

  it("opens a user engine's drawer straight away", () => {
    renderTab();
    const trinoRow = within(screen.getByRole("list", { name: "Your engines" })).getByText("trino-prod").closest("li") as HTMLElement;
    fireEvent.click(within(trinoRow).getByRole("button", { name: "Edit" }));
    expect(setDestinationDrawer).toHaveBeenCalledWith(trino);
  });
});
