import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { PathStepRead, PathToARead } from "../../../api/pathToA";
import { PathToAPanel } from "./PathToAPanel";

const { getPathToA, applyPathStep, auth } = vi.hoisted(() => ({
  getPathToA: vi.fn(),
  applyPathStep: vi.fn(),
  auth: { role: "admin" }
}));

vi.mock("../../../api/pathToA", () => ({ getPathToA, applyPathStep }));

vi.mock("../../../lib/auth", () => ({
  useAuth: () => ({ api: { token: "t" }, token: "t", role: auth.role, subject: "someone" })
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const step = (overrides: Partial<PathStepRead>): PathStepRead => ({
  id: "expire_snapshots",
  kind: "automatic",
  title: "Expire old snapshots",
  detail: "Keeps the last 2 days of history.",
  fixes: ["Snapshot retention buildup"],
  projected_score: 52,
  projected_grade: "D",
  confidence: "estimated",
  evidence: ["364 snapshots"],
  action: { type: "run_operations", operations: ["expire_snapshots"] },
  required_role: "operator",
  confirm_text: null,
  in_schedule: false,
  ...overrides
});

const path: PathToARead = {
  table_id: "table-1",
  score: 32,
  grade: "F",
  deductions: [],
  steps: [
    step({}),
    step({
      id: "retention_hours",
      kind: "approval",
      title: "Keep 15 hours of snapshot history",
      projected_score: 64,
      projected_grade: "C",
      action: { type: "set_overrides", overrides: { retention_hours: 15 } },
      required_role: "admin"
    }),
    step({
      id: "partition_spec",
      kind: "design",
      title: "Stop partitioning by kind",
      fixes: ["Partition volume skew"],
      projected_score: 92,
      projected_grade: "A",
      action: { type: "partition_spec", remove: ["kind"], add: [] },
      required_role: "admin",
      confirm_text: "events"
    }),
    step({
      id: "streaming_writer",
      kind: "external",
      title: "Slow the writer's commit rate",
      fixes: [],
      projected_score: 100,
      projected_grade: "A",
      action: null,
      required_role: null,
      evidence: []
    })
  ],
  cryo_score: 92,
  cryo_grade: "A",
  reachable_score: 100,
  reachable_grade: "A",
  notes: ["Estimates use the 5,000 partitions recorded by the last scan."]
};

const renderPanel = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <PathToAPanel tableId="table-1" />
    </QueryClientProvider>
  );

describe("PathToAPanel", () => {
  beforeEach(() => {
    auth.role = "admin";
    getPathToA.mockReset().mockResolvedValue(path);
    applyPathStep.mockReset().mockResolvedValue({ step_id: "x", status: "applied", message: "Done" });
  });

  it("groups steps by who acts and shows the grade after each", async () => {
    renderPanel();
    expect(await screen.findByText("Cryo does this")).toBeInTheDocument();
    expect(screen.getByText("Needs your approval")).toBeInTheDocument();
    expect(screen.getByText("Layout changes")).toBeInTheDocument();
    expect(screen.getByText("Outside Cryo")).toBeInTheDocument();
    const summary = screen.getByText("→ after Cryo's steps").parentElement as HTMLElement;
    expect(within(summary).getByText("F · 32")).toBeInTheDocument();
    expect(within(summary).getByText("A · 92")).toBeInTheDocument();
    expect(screen.getByText("Estimates use the 5,000 partitions recorded by the last scan.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Slow the writer/ })).not.toBeInTheDocument();
  });

  it("runs an automatic step after confirmation", async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "Run now" }));
    fireEvent.click(await screen.findByRole("button", { name: "Run now" }));
    await waitFor(() =>
      expect(applyPathStep).toHaveBeenCalledWith({ token: "t" }, "table-1", {
        step_id: "expire_snapshots",
        confirm: undefined
      })
    );
  });

  it("needs the table name typed before a layout change", async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "Change partition spec" }));
    const dialog = await screen.findByRole("alertdialog");
    const confirm = within(dialog).getByRole("button", { name: "Change partition spec" });
    expect(confirm).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText("Type events to confirm"), { target: { value: "events" } });
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);
    await waitFor(() =>
      expect(applyPathStep).toHaveBeenCalledWith({ token: "t" }, "table-1", {
        step_id: "partition_spec",
        confirm: "events"
      })
    );
  });

  it("explains the role a viewer lacks instead of offering the button", async () => {
    auth.role = "operator";
    renderPanel();
    expect(await screen.findByRole("button", { name: "Run now" })).toBeInTheDocument();
    expect(screen.getByText("Needs the admin role to save for this table.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Change partition spec" })).not.toBeInTheDocument();
  });

  it("lists design findings beside the grade without deducting them", async () => {
    getPathToA.mockResolvedValue({
      ...path,
      design_findings: [
        { label: "Partition volume skew", severity: "critical", reason: "largest partition is 300x the median" }
      ]
    });
    renderPanel();
    const section = await screen.findByLabelText("Design findings");
    expect(section).toHaveTextContent("Partition volume skew");
    expect(section).toHaveTextContent("don't lower the grade");
  });

  it("says so when there is nothing to fix", async () => {
    getPathToA.mockResolvedValue({ ...path, steps: [], score: 100, grade: "A" });
    renderPanel();
    expect(await screen.findByText("No deductions. This table is already at its best grade.")).toBeInTheDocument();
  });
});
