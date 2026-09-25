import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { RunRead } from "../../api/types";
import { RunDetail } from "./RunDetail";

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
  getRun: vi.fn(),
  getRunJobs: vi.fn(),
  listRuns: vi.fn(),
  cancelRun: vi.fn(),
  diagnoseRunFailure: vi.fn(),
  forceAdmitJob: vi.fn()
}));

const mockUseRunLiveEvents = vi.fn();
vi.mock("../../hooks/useRunLiveEvents", () => ({
  useRunLiveEvents: (...args: unknown[]) => mockUseRunLiveEvents(...args),
  isTerminalRunStatus: (status?: string | null) =>
    Boolean(status && ["succeeded", "failed", "cancelled", "partial_failed"].includes(status))
}));

import { getRun, getRunJobs, listRuns } from "../../api/client";

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

  it("renders a live log console with search controls wired to live messages", async () => {
    mockUseRunLiveEvents.mockReturnValue({
      ...liveEventsDefault,
      messages: [{ ts: new Date().toISOString(), message: "Optimizing table foo", level: "info" }]
    });
    renderPage();
    expect(await screen.findByText("Optimizing table foo")).toBeInTheDocument();
    expect(screen.getByLabelText("Search log")).toBeInTheDocument();
  });
});
