import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { JobRead, RunRead } from "../../api/types";
import { RunDetail } from "./RunDetail";

const authRole = vi.hoisted(() => ({ current: "admin" }));

vi.mock("../../lib/auth", () => ({
  useAuth: () => ({
    api: { token: "test-token" },
    token: "test-token",
    role: authRole.current,
    subject: "tester",
    loading: false,
    error: null,
    isAuthenticated: true,
    signIn: vi.fn(),
    signOut: vi.fn()
  })
}));

vi.mock("../../api/client", () => ({
  getRun: vi.fn(),
  getRunJobs: vi.fn(),
  listRuns: vi.fn(),
  cancelRun: vi.fn(),
  diagnoseRunFailure: vi.fn(),
  forceAdmitJob: vi.fn()
}));

const mockUseRunLiveEvents = vi.fn();
vi.mock("../../hooks/useRunLiveEvents", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../hooks/useRunLiveEvents")>()),
  useRunLiveEvents: (...args: unknown[]) => mockUseRunLiveEvents(...args)
}));

import { getRun, getRunJobs, listRuns } from "../../api/client";
import { formatBytesGb, formatGb, formatNumber } from "../../lib/format";

const baseRun: RunRead = {
  id: "22222222-2222-2222-2222-222222222222",
  kind: "maintenance",
  status: "running",
  created_at: new Date().toISOString(),
  summary: { target_label: "nightly-preset" }
};

const liveEventsDefault = {
  messages: [] as Array<{ ts?: string; message?: string; level?: string; job_id?: string }>,
  snapshot: null as { run?: RunRead; jobs?: unknown[] } | null,
  wsOpen: true,
  wsExhausted: false,
  runTerminal: false,
  reconnectAttempt: 0,
  manualReconnect: vi.fn()
};

const job = (overrides: Partial<JobRead>): JobRead => ({
  id: "33333333-3333-3333-3333-333333333333",
  run_id: baseRun.id,
  table_id: "44444444-4444-4444-4444-444444444444",
  destination_id: "55555555-5555-5555-5555-555555555555",
  mode: "standard",
  status: "succeeded",
  ...overrides
});

const renderPage = (runId = baseRun.id) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/operations/runs/${runId}`]}>
        <Routes>
          <Route path="/operations/runs/:runId" element={<RunDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe("RunDetail", () => {
  beforeEach(() => {
    authRole.current = "admin";
    vi.clearAllMocks();
    vi.mocked(getRun).mockResolvedValue(baseRun);
    vi.mocked(getRunJobs).mockResolvedValue([]);
    vi.mocked(listRuns).mockResolvedValue([]);
    mockUseRunLiveEvents.mockReturnValue(liveEventsDefault);
  });

  it("shows measured bytes freed and does not invent a zero", async () => {
    vi.mocked(getRun).mockResolvedValue({
      ...baseRun,
      realized_bytes_freed: 1024 ** 3,
      realized_actions_measured: 1,
      realized_actions_succeeded: 2
    });

    renderPage();

    expect(await screen.findByText(/Freed 1 GB \(1 of 2 actions measured\)/)).toBeInTheDocument();
  });

  it("does not show a stalled label when the run has recent activity", async () => {
    renderPage();
    await screen.findByRole("heading", { name: /Run 22222222/ });
    expect(screen.queryByText(/may be stalled/i)).not.toBeInTheDocument();
  });

  it("shows a stalled label when an active run has had no update for over the threshold", async () => {
    const staleTs = new Date(Date.now() - 5 * 60 * 1000).toISOString();
    vi.mocked(getRun).mockResolvedValue({ ...baseRun, created_at: staleTs, status: "running" });
    mockUseRunLiveEvents.mockReturnValue({
      ...liveEventsDefault,
      messages: [{ ts: staleTs, message: "still scanning" }]
    });
    renderPage();
    await waitFor(() => {
      expect(screen.getByText(/may be stalled/i)).toBeInTheDocument();
    });
  });

  it("does not show a stalled label once the run is finished", async () => {
    const staleTs = new Date(Date.now() - 5 * 60 * 1000).toISOString();
    vi.mocked(getRun).mockResolvedValue({
      ...baseRun,
      created_at: staleTs,
      status: "succeeded",
      finished_at: staleTs
    });
    mockUseRunLiveEvents.mockReturnValue({
      ...liveEventsDefault,
      runTerminal: true,
      messages: [{ ts: staleTs, message: "done" }]
    });
    renderPage();
    await screen.findByRole("heading", { name: /Run 22222222/ });
    expect(screen.queryByText(/may be stalled/i)).not.toBeInTheDocument();
  });

  it("offers no Cancel on a finished partial run with nothing left to stop", async () => {
    vi.mocked(getRun).mockResolvedValue({
      ...baseRun,
      status: "partial",
      finished_at: new Date().toISOString()
    });
    vi.mocked(getRunJobs).mockResolvedValue([job({ status: "succeeded" })]);

    renderPage();

    await waitFor(() => {
      expect(screen.queryByRole("button", { name: /^Cancel/ })).not.toBeInTheDocument();
    });
  });

  it("offers to stop only the jobs a finished run still owns", async () => {
    vi.mocked(getRun).mockResolvedValue({
      ...baseRun,
      status: "partial",
      finished_at: new Date().toISOString()
    });
    vi.mocked(getRunJobs).mockResolvedValue([
      job({ status: "succeeded" }),
      job({ id: "66666666-6666-6666-6666-666666666666", status: "deferred" })
    ]);

    renderPage();

    expect(await screen.findByRole("button", { name: "Cancel remaining jobs (1)" })).toBeInTheDocument();
  });

  it("disables Cancel for a viewer and says why", async () => {
    authRole.current = "viewer";
    vi.mocked(getRun).mockResolvedValue({ ...baseRun, status: "running" });
    renderPage();
    const cancel = await screen.findByRole("button", { name: "Cancel" });
    expect(cancel).toBeDisabled();
    expect(screen.getByText(/Viewer access is read-only/)).toBeInTheDocument();
  });

  it("renders a live log console with search controls wired to live messages", async () => {
    mockUseRunLiveEvents.mockReturnValue({
      ...liveEventsDefault,
      messages: [{ ts: new Date().toISOString(), message: "Optimizing table foo", level: "info" }]
    });
    renderPage();
    expect(await screen.findByText("Optimizing table foo")).toBeInTheDocument();
    expect(screen.getByLabelText("Search log")).toBeInTheDocument();
  });

  it("does not render batch summary blocks for a non-batch run", async () => {
    renderPage();
    await screen.findByRole("heading", { name: /Run 22222222/ });
    expect(screen.queryByText("Over budget")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Batch summary" })).not.toBeInTheDocument();
  });

  it("renders every batch summary block from the run report", async () => {
    vi.mocked(getRun).mockResolvedValue({
      ...baseRun,
      kind: "scheduled",
      status: "partial",
      finished_at: new Date().toISOString(),
      summary: {
        pipeline: "batch",
        selected: 10,
        succeeded: 7,
        failed: 2,
        skipped: 1,
        over_budget: 3,
        files_before: 1000,
        files_after: 400,
        size_before_gb: 10,
        size_after_gb: 8,
        bytes_rewritten: 2 * 1024 ** 3,
        lanes: [{ lane: 0, cpu_milli: 2000, mem_mib: 8192, tables: 4 }],
        autocomp: { budget_gbhr: 40, chosen_gbhr: 12.5 },
        failures: [{ table: "db.events", error: "boom" }]
      }
    });

    renderPage();

    const summary = await screen.findByRole("region", { name: "Batch summary" });
    expect(within(summary).getByText("Selected").closest(".cryo-metric-tile")).toHaveTextContent("10");
    expect(within(summary).getByText("Succeeded").closest(".cryo-metric-tile")).toHaveTextContent("7");
    expect(within(summary).getByText("Failed").closest(".cryo-metric-tile")).toHaveTextContent("2");
    expect(within(summary).getByText("Skipped").closest(".cryo-metric-tile")).toHaveTextContent("1");
    expect(within(summary).getByText("Over budget").closest(".cryo-metric-tile")).toHaveTextContent("3");
    expect(
      within(summary).getByLabelText("Eligible but not chosen: run's compute budget was spent")
    ).toBeInTheDocument();
    expect(
      within(summary).getByText(`${formatNumber(1000)} → ${formatNumber(400)} (-60%)`)
    ).toBeInTheDocument();
    expect(within(summary).getByText(`${formatGb(10)} → ${formatGb(8)} (-20%)`)).toBeInTheDocument();
    expect(within(summary).getByText(formatBytesGb(2 * 1024 ** 3))).toBeInTheDocument();
    expect(within(summary).getByRole("columnheader", { name: "vCPU" })).toBeInTheDocument();
    expect(within(summary).getByRole("cell", { name: "8 GiB" })).toBeInTheDocument();
    expect(within(summary).getByText("12.5 of 40 GB·h")).toBeInTheDocument();
    expect(within(summary).getByText("db.events")).toBeInTheDocument();
    expect(within(summary).getAllByText("boom").length).toBeGreaterThan(0);
  });

  it("renders Unknown for a batch summary with missing keys and never a zero", async () => {
    vi.mocked(getRun).mockResolvedValue({
      ...baseRun,
      kind: "scheduled",
      status: "succeeded",
      finished_at: new Date().toISOString(),
      summary: { pipeline: "batch" }
    });

    renderPage();

    const summary = await screen.findByRole("region", { name: "Batch summary" });
    const selected = within(summary).getByText("Selected").closest(".cryo-metric-tile");
    expect(selected).toHaveTextContent("Unknown");
    expect(selected).not.toHaveTextContent("0");
    expect(within(summary).queryByText("0 GB")).not.toBeInTheDocument();
    expect(within(summary).getByText("Unknown of Unknown GB·h")).toBeInTheDocument();
    expect(within(summary).getAllByText("Unknown").length).toBeGreaterThan(0);
  });

  it("pages a 1500-job run at 50 rows and chip counts add up", async () => {
    const statuses = ["failed", "succeeded", "skipped", "deferred"] as const;
    vi.mocked(getRunJobs).mockResolvedValue(
      Array.from({ length: 1500 }, (_, index) =>
        job({
          id: `job-${index}`,
          status: statuses[index % 4],
          schema_name: "db",
          table_name: index < 3 ? `needle-${index}` : `other-${index}`
        })
      )
    );

    renderPage();

    await screen.findByText("1 / 30");
    const detailLinks = screen.getAllByRole("link", { name: "Detail" });
    expect(detailLinks.length).toBeGreaterThan(0);
    expect(detailLinks.length).toBeLessThanOrEqual(50);
    const chips = screen.getAllByTestId("job-status-chip");
    expect(chips[0]).toHaveTextContent(/Failed/i);
    const sum = chips.reduce((total, chip) => total + Number(chip.getAttribute("data-count")), 0);
    expect(sum).toBe(1500);
  });

  it("narrows job rows with the table-name filter", async () => {
    const statuses = ["failed", "succeeded", "skipped", "deferred"] as const;
    vi.mocked(getRunJobs).mockResolvedValue(
      Array.from({ length: 1500 }, (_, index) =>
        job({
          id: `job-${index}`,
          status: statuses[index % 4],
          schema_name: "db",
          table_name: index < 3 ? `needle-${index}` : `other-${index}`
        })
      )
    );

    renderPage();
    await screen.findByText("1 / 30");
    expect(screen.queryByText("db.needle-1")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Filter by table name"), { target: { value: "needle" } });

    await waitFor(() => {
      expect(screen.getAllByRole("link", { name: "Detail" })).toHaveLength(3);
    });
    expect(screen.getAllByText("db.needle-1").length).toBeGreaterThan(0);
    expect(screen.queryByText("db.other-10")).not.toBeInTheDocument();
  });

  it("marks optimize when the stored plan operation is an object with type", async () => {
    vi.mocked(getRun).mockResolvedValue({
      ...baseRun,
      status: "succeeded",
      finished_at: new Date().toISOString()
    });
    vi.mocked(getRunJobs).mockResolvedValue([
      job({ status: "succeeded", plan: { operations: [{ type: "optimize", params: {} }] } })
    ]);

    renderPage();

    await waitFor(() => {
      const pipeline = screen.getByRole("list", { name: "Maintenance pipeline" });
      const label = within(pipeline).getByText("optimize", { selector: ".ops-pipeline__label" });
      expect(label.closest('[role="listitem"]')).toHaveTextContent("succeeded");
    });
    const pipeline = screen.getByRole("list", { name: "Maintenance pipeline" });
    expect(within(pipeline).queryByText("queued")).not.toBeInTheDocument();
    expect(screen.queryByText("queued")).not.toBeInTheDocument();
  });

  it("also marks optimize when the plan stores the operation as a string", async () => {
    vi.mocked(getRun).mockResolvedValue({
      ...baseRun,
      status: "succeeded",
      finished_at: new Date().toISOString()
    });
    vi.mocked(getRunJobs).mockResolvedValue([
      job({ status: "succeeded", plan: { operations: ["optimize"] } })
    ]);

    renderPage();

    await waitFor(() => {
      const pipeline = screen.getByRole("list", { name: "Maintenance pipeline" });
      const label = within(pipeline).getByText("optimize", { selector: ".ops-pipeline__label" });
      expect(label.closest('[role="listitem"]')).toHaveTextContent("succeeded");
    });
  });

  it("treats a partial job as partly succeeded for its operation", async () => {
    vi.mocked(getRunJobs).mockResolvedValue([
      job({ status: "partial", plan: { operations: [{ type: "optimize" }] } })
    ]);

    renderPage();

    await waitFor(() => {
      const pipeline = screen.getByRole("list", { name: "Maintenance pipeline" });
      const label = within(pipeline).getByText("optimize", { selector: ".ops-pipeline__label" });
      expect(label.closest('[role="listitem"]')).toHaveTextContent("partial");
    });
    expect(within(screen.getByRole("list", { name: "Maintenance pipeline" })).queryByText("queued")).not.toBeInTheDocument();
  });

  it("does not invent queued steps on a finished run without per-operation evidence", async () => {
    vi.mocked(getRun).mockResolvedValue({
      ...baseRun,
      status: "succeeded",
      finished_at: new Date().toISOString()
    });
    vi.mocked(getRunJobs).mockResolvedValue([job({ status: "succeeded", plan: { operations: [] } })]);

    renderPage();

    await waitFor(() => {
      const pipeline = screen.getByRole("list", { name: "Maintenance pipeline" });
      expect(within(pipeline).getAllByText("Not recorded")).toHaveLength(4);
    });
    const pipeline = screen.getByRole("list", { name: "Maintenance pipeline" });
    expect(within(pipeline).queryByText("queued")).not.toBeInTheDocument();
    expect(within(pipeline).queryByText("Waiting")).not.toBeInTheDocument();
    expect(screen.queryByText("queued")).not.toBeInTheDocument();
  });

  it("shows Waiting for steps with no evidence while the run is running", async () => {
    renderPage();

    const pipeline = await screen.findByRole("list", { name: "Maintenance pipeline" });
    expect(within(pipeline).getAllByText("Waiting")).toHaveLength(4);
    expect(within(pipeline).queryByText("queued")).not.toBeInTheDocument();
    expect(screen.queryByText("queued")).not.toBeInTheDocument();
  });
});
