import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { QueryPerformanceData } from "../api/client";
import { QueryPerformance } from "./QueryPerformance";

vi.mock("../lib/auth", () => ({
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

vi.mock("../api/client", () => ({
  getQueryPerformanceDashboard: vi.fn()
}));

import { getQueryPerformanceDashboard } from "../api/client";

const renderPage = (initialEntries: string[] = ["/query-performance"]) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={initialEntries}>
        <QueryPerformance />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

const queryRow = (overrides: Partial<QueryPerformanceData["queries"][number]> = {}): QueryPerformanceData["queries"][number] => ({
  query_id: "q-1",
  query_text: "SELECT * FROM analytics.events WHERE event_time > now() - interval '1' day",
  engine: "trino",
  user: "analyst",
  duration_ms: 1200,
  bytes_scanned: 1024 * 1024 * 512,
  rows_produced: 42,
  status: "succeeded",
  started_at: "2026-02-01T00:00:00Z",
  table_name: "analytics.events",
  table_id: "table-1",
  ...overrides
});

const dashboard = (overrides: Partial<QueryPerformanceData> = {}): QueryPerformanceData => ({
  queries: [queryRow()],
  total: 1,
  p50_latency_ms: 100,
  p95_latency_ms: 500,
  p99_latency_ms: 900,
  avg_bytes_scanned: 1024 * 1024,
  failed_count: 0,
  ...overrides
});

describe("QueryPerformance", () => {
  it("labels the engine and time-range selectors", async () => {
    vi.mocked(getQueryPerformanceDashboard).mockResolvedValue(dashboard());
    renderPage();
    await screen.findByText("analytics.events");

    expect(screen.getByLabelText("Engine")).toBeInTheDocument();
    expect(screen.getByLabelText("Time range")).toBeInTheDocument();
  });

  it("encodes the engine and time-range selection in the URL and refetches", async () => {
    vi.mocked(getQueryPerformanceDashboard).mockResolvedValue(dashboard());
    renderPage();
    await screen.findByText("analytics.events");

    fireEvent.change(screen.getByLabelText("Engine"), { target: { value: "spark" } });

    await waitFor(() => {
      expect(getQueryPerformanceDashboard).toHaveBeenCalledWith(expect.anything(), 24, "spark");
    });
  });

  it("restores the engine and time-range selection from the URL on load", async () => {
    vi.mocked(getQueryPerformanceDashboard).mockResolvedValue(dashboard());
    renderPage(["/query-performance?engine=athena&hours=168"]);

    await waitFor(() => {
      expect(getQueryPerformanceDashboard).toHaveBeenCalledWith(expect.anything(), 168, "athena");
    });
    expect(screen.getByLabelText("Engine")).toHaveValue("athena");
    expect(screen.getByLabelText("Time range")).toHaveValue("168");
  });

  it("opens an accessible detail view with the full query text", async () => {
    vi.mocked(getQueryPerformanceDashboard).mockResolvedValue(
      dashboard({
        queries: [
          queryRow({
            query_text:
              "SELECT * FROM analytics.events WHERE event_time > now() - interval '1' day AND status = 'active' ORDER BY event_time DESC LIMIT 500"
          })
        ]
      })
    );
    renderPage();
    await screen.findByText("analytics.events");

    fireEvent.click(screen.getAllByRole("button", { name: "Details" })[0]);

    expect(await screen.findByText("Query detail")).toBeInTheDocument();
    expect(
      screen.getByText(
        "SELECT * FROM analytics.events WHERE event_time > now() - interval '1' day AND status = 'active' ORDER BY event_time DESC LIMIT 500"
      )
    ).toBeInTheDocument();
  });

  it("copies the full query text to the clipboard from the detail view", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    vi.mocked(getQueryPerformanceDashboard).mockResolvedValue(dashboard());
    renderPage();
    await screen.findByText("analytics.events");

    fireEvent.click(screen.getAllByRole("button", { name: "Details" })[0]);
    await screen.findByText("Query detail");
    fireEvent.click(screen.getByRole("button", { name: /Copy/ }));

    expect(writeText).toHaveBeenCalledWith(queryRow().query_text);
    expect(await screen.findByText("Copied")).toBeInTheDocument();
  });

  it("shows the telemetry-required notice only when there are truly zero results", async () => {
    vi.mocked(getQueryPerformanceDashboard).mockResolvedValue(
      dashboard({ queries: [], total: 0, failed_count: 0 })
    );
    renderPage();

    expect(await screen.findByText(/Query performance/i)).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByText("analytics.events")).not.toBeInTheDocument();
    });
  });
});
