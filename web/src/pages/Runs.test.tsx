import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { Runs } from "./Runs";

vi.mock("../lib/auth", () => ({
  useAuth: () => ({
    api: { token: "test-token" },
    token: "test-token",
    role: "operator",
    subject: "tester",
    loading: false,
    error: null,
    isAuthenticated: true,
    signIn: vi.fn(),
    signOut: vi.fn()
  })
}));

vi.mock("../components/RunConsole", () => ({
  RunConsole: () => <div>Mock run console</div>
}));

vi.mock("../api/client", () => ({
  listRuns: vi.fn().mockResolvedValue([
    {
      id: "run-12345678-abcd-efgh",
      kind: "maintenance",
      status: "running",
      trigger: "web",
      created_at: "2026-07-01T00:00:00Z",
      summary: { jobs_total: 2, target_label: "demo.public.events" }
    }
  ]),
  getRunJobs: vi.fn().mockResolvedValue([
    {
      id: "job-12345678-abcd-efgh",
      table_id: "table-1",
      schema_name: "public",
      table_name: "events",
      status: "running",
      mode: "standard"
    }
  ]),
  cancelRun: vi.fn()
}));

describe("Runs page", () => {
  it("renders runs list and live console when a run is selected", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/runs/run-12345678-abcd-efgh"]}>
          <Routes>
            <Route path="/runs/:runId?" element={<Runs />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );
    expect(await screen.findByRole("heading", { name: "Runs" })).toBeInTheDocument();
    expect(await screen.findByText("Recent runs")).toBeInTheDocument();
    expect(await screen.findByText("Mock run console")).toBeInTheDocument();
  });
});
