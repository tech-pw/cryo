import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MaintenanceActions } from "./MaintenanceActions";
import { OperationLauncherProvider, useOperationLauncher } from "./OperationLauncher";
import type { RunnableOperation } from "./operationCatalog";

const mocks = vi.hoisted(() => ({
  getEffectivePreset: vi.fn(),
  getEffectiveParams: vi.fn(),
  getTable: vi.fn(),
  runTableOperations: vi.fn()
}));

vi.mock("../../../api/client", () => mocks);

vi.mock("../../../lib/auth", () => ({
  useAuth: () => ({ api: { token: "t" }, token: "t", role: "operator" })
}));

const access = vi.hoisted(() => ({ allowed: true, hidden: false, reason: null as string | null }));
vi.mock("../../../lib/useWriteControl", () => ({ useWriteControl: () => access }));

const renderCart = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={["/tables/t1"]}>
        <OperationLauncherProvider tableId="t1" tableName="events">
          <MaintenanceActions tableId="t1" stats={{}} audit={[]} orphanBytes={null} deductions={[]} />
        </OperationLauncherProvider>
      </MemoryRouter>
    </QueryClientProvider>
  );

const LaunchButton = ({ op }: { op: RunnableOperation }) => {
  const launcher = useOperationLauncher();
  return (
    <button type="button" onClick={() => launcher?.launch(op)}>
      launch {op}
    </button>
  );
};

const renderWithLaunch = (op: RunnableOperation) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={["/tables/t1"]}>
        <OperationLauncherProvider tableId="t1" tableName="events">
          <MaintenanceActions tableId="t1" stats={{}} audit={[]} orphanBytes={null} deductions={[]} />
          <LaunchButton op={op} />
        </OperationLauncherProvider>
      </MemoryRouter>
    </QueryClientProvider>
  );

const addButton = (title: string) =>
  within(screen.getByText(title).closest("li") as HTMLElement).getByRole("button", { name: /Add to run|Added to run/ });

describe("run cart", () => {
  beforeEach(() => {
    access.allowed = true;
    access.reason = null;
    mocks.getEffectivePreset.mockReset().mockResolvedValue({ table_id: "t1", preset_id: "p1", preset_name: "nightly", provenance: "assignment" });
    mocks.getEffectiveParams.mockReset().mockResolvedValue({ table_id: "t1", preset_params: {}, param_overrides: {}, effective_params: {} });
    mocks.getTable.mockReset().mockResolvedValue({ id: "t1", stats: {} });
    mocks.runTableOperations.mockReset().mockResolvedValue({ run_id: "run-1", job_ids: ["j"], status: "queued" });
  });

  it("collects operations, shows them in execution order, and submits one ordered run", async () => {
    renderCart();
    await waitFor(() => expect(addButton("Remove orphan files")).toBeEnabled());

    fireEvent.click(addButton("Remove orphan files"));
    fireEvent.click(addButton("Compact files"));
    expect(addButton("Compact files")).toHaveAttribute("aria-pressed", "true");

    const bar = screen.getByRole("region", { name: "Run cart" });
    expect(within(bar).getByText("Compact files → Remove orphan files")).toBeInTheDocument();
    fireEvent.click(within(bar).getByRole("button", { name: "Review & run" }));

    const dialog = await screen.findByRole("dialog");
    const steps = [...dialog.querySelectorAll(".td-cart-steps li b")].map((node) => node.textContent);
    expect(steps).toEqual(["Compact files", "Remove orphan files"]);
    const run = within(dialog).getByRole("button", { name: "Queue 2 operations" });
    expect(run).toBeDisabled();
    fireEvent.click(within(dialog).getByRole("checkbox", { name: /files are deleted/ }));
    fireEvent.click(run);

    await waitFor(() =>
      expect(mocks.runTableOperations).toHaveBeenCalledWith({ token: "t" }, "t1", { operations: ["optimize", "remove_orphan_files"] })
    );
    await waitFor(() => expect(screen.queryByRole("region", { name: "Run cart" })).not.toBeInTheDocument());
  });

  it("keeps non-destructive runs free of the delete acknowledgement", async () => {
    renderCart();
    await waitFor(() => expect(addButton("Rewrite manifests")).toBeEnabled());
    fireEvent.click(addButton("Rewrite manifests"));
    fireEvent.click(screen.getByRole("button", { name: "Review & run" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByRole("checkbox", { name: /files are deleted/ })).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Queue Rewrite manifests" })).toBeEnabled();
  });

  it("sends force only when the operator opts in to running already-queued steps", async () => {
    renderCart();
    await waitFor(() => expect(addButton("Rewrite manifests")).toBeEnabled());
    fireEvent.click(addButton("Rewrite manifests"));
    fireEvent.click(screen.getByRole("button", { name: "Review & run" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("checkbox", { name: /Run even if already queued/ }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Queue Rewrite manifests" }));

    await waitFor(() =>
      expect(mocks.runTableOperations).toHaveBeenCalledWith({ token: "t" }, "t1", { operations: ["optimize_manifests"], force: true })
    );
  });

  it("disables adding with the role's reason", async () => {
    access.allowed = false;
    access.reason = "Viewers can't run maintenance";
    renderCart();
    expect(await screen.findByText("Viewers can't run maintenance")).toBeInTheDocument();
    expect(addButton("Compact files")).toBeDisabled();
  });

  it("launching one operation submits only that operation and keeps the cart", async () => {
    renderWithLaunch("expire_snapshots");
    await waitFor(() => expect(addButton("Remove orphan files")).toBeEnabled());
    fireEvent.click(addButton("Compact files"));
    fireEvent.click(addButton("Remove orphan files"));

    fireEvent.click(screen.getByRole("button", { name: "launch expire_snapshots" }));
    const dialog = await screen.findByRole("dialog");
    const steps = [...dialog.querySelectorAll(".td-cart-steps li b")].map((node) => node.textContent);
    expect(steps).toEqual(["Expire snapshots"]);
    expect(within(dialog).getByText(/Your run cart \(2 operations\) is not part of this run/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("checkbox", { name: /files are deleted/ }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Queue Expire snapshots" }));

    await waitFor(() => expect(mocks.runTableOperations).toHaveBeenCalledWith({ token: "t" }, "t1", { operations: ["expire_snapshots"] }));
    expect(mocks.runTableOperations).toHaveBeenCalledTimes(1);
    const bar = await screen.findByRole("region", { name: "Run cart" });
    expect(within(bar).getByText("Compact files → Remove orphan files")).toBeInTheDocument();
  });

  it("cancelling a single launch leaves the cart review unchanged", async () => {
    renderWithLaunch("expire_snapshots");
    await waitFor(() => expect(addButton("Rewrite manifests")).toBeEnabled());
    fireEvent.click(addButton("Rewrite manifests"));
    fireEvent.click(screen.getByRole("button", { name: "launch expire_snapshots" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Review & run" }));
    const cartDialog = await screen.findByRole("dialog");
    const steps = [...cartDialog.querySelectorAll(".td-cart-steps li b")].map((node) => node.textContent);
    expect(steps).toEqual(["Rewrite manifests"]);
  });
});
