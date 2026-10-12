import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getDatalakeDashboard, getFastPathFindings, getScannerStatus, listPendingApprovals, listRuns } from "../api/client";
import { getCryoVitals } from "../api/vitals";
import { getEngineLoad } from "../api/engineLoad";
import { DatalakeHealth, tablesAtGradeDf } from "./DatalakeHealth";

vi.mock("../lib/auth", () => ({ useAuth: () => ({ api: { token: "test-token" }, token: "test-token", role: "operator" }) }));
vi.mock("../api/client", () => ({
  ApiError: class ApiError extends Error {},
  getDatalakeDashboard: vi.fn(), getScannerStatus: vi.fn(), getFastPathFindings: vi.fn(),
  listRuns: vi.fn(), listPendingApprovals: vi.fn()
}));
vi.mock("../api/vitals", () => ({ getCryoVitals: vi.fn() }));
vi.mock("../api/engineLoad", () => ({ getEngineLoad: vi.fn() }));

const dashboard = {
  totals: { tables: 5, size_gb: 10, small_files: 100, needs_compaction: 1 },
  grade_distribution: [{ grade: "B", count: 1 }, { grade: "D", count: 1 }],
  freshness_distribution: [{ key: "3_7d" as const, label: "Not updated 3-7 days", count: 5 }],
  freshness_as_of: "2026-09-28T12:00:00Z", top_offenders: [], cost_by_schema: [], drift: []
};
beforeEach(() => {
  vi.mocked(getDatalakeDashboard).mockReset().mockResolvedValue(dashboard);
  vi.mocked(getCryoVitals).mockReset().mockResolvedValue({ score: 91, status: "HEALTHY", deductions: [{ reason: "dispatch: stalled", points: 8 }], impact: { measurement_state: "unmeasured", gb_freed_yesterday: null } });
  vi.mocked(listPendingApprovals).mockReset().mockResolvedValue([{ id: "a1", table_id: "t1", table_name: "db.t", operation_type: "expire_snapshots", status: "pending", requested_by: "governor", impact_preview: null, created_at: "2026-09-28T00:00:00Z", expires_at: null }]);
  vi.mocked(listRuns).mockReset().mockResolvedValue([]);
  vi.mocked(getScannerStatus).mockReset().mockResolvedValue({ active_run: null, queue_depth: 0 } as Awaited<ReturnType<typeof getScannerStatus>>);
  vi.mocked(getFastPathFindings).mockReset().mockResolvedValue([]);
  vi.mocked(getEngineLoad).mockReset().mockResolvedValue({ engines: [] });
});
const renderHome = () => render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, retryDelay: 0 } } })}><MemoryRouter><DatalakeHealth /></MemoryRouter></QueryClientProvider>);

describe("Home overview", () => {
  it("separates lake condition, platform health, maintenance and impact", async () => {
    renderHome();
    expect(await screen.findByText(/tables at D or F/)).toBeInTheDocument();
    for (const name of ["Lake health", "Cryo platform", "Latest nightly maintenance", "Storage impact", "Table freshness", "Your next steps"]) {
      expect(screen.getByRole("region", { name })).toBeInTheDocument();
    }
    expect(screen.getByRole("img", { name: /Cryo platform health: 91 out of 100/ })).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Lake health" })).queryByText("/ 100")).not.toBeInTheDocument();
    expect(screen.getByText("No nightly run")).toBeInTheDocument();
  });
  it("uses precise table filters and preserves freshness time boundaries", async () => {
    renderHome();
    expect(await screen.findByRole("link", { name: /tables at D or F/ })).toHaveAttribute("href", "/tables?grade=D,F");
    expect(screen.getByRole("link", { name: /1 table needs compaction/ })).toHaveAttribute("href", "/tables?needs=compaction");
    const stale = screen.getByRole("link", { name: /Not updated 3-7 days/ });
    expect(stale).toHaveAttribute("href", "/catalogs?freshness=3_7d&freshnessAsOf=2026-09-28T12%3A00%3A00Z");
    stale.focus();
    expect(stale).toHaveFocus();
  });
  it("announces health and compaction counts without hiding ungraded tables", async () => {
    renderHome();
    await screen.findByText(/tables at D or F/);
    expect(screen.getByRole("status")).toHaveTextContent("1 tables at grade D or F");
    expect(screen.getByRole("status")).toHaveTextContent("1 tables need compaction");
    expect(screen.getByText("Not graded").closest("li")).toHaveTextContent("3");
  });
  it("keeps both missing reclaim measurements unknown instead of zero", async () => {
    renderHome();
    const impact = await screen.findByRole("region", { name: "Storage impact" });
    await waitFor(() => expect(within(impact).getAllByText("Not measured")).toHaveLength(2));
    expect(impact).not.toHaveTextContent("0 GB");
    expect(screen.queryByText("Monthly savings")).not.toBeInTheDocument();
  });
  it("keeps partial evidence and estimated opportunity distinct", async () => {
    vi.mocked(getCryoVitals).mockResolvedValue({ impact: { measurement_state: "partial", gb_freed_yesterday: 12, measured: 2, succeeded: 4 } });
    vi.mocked(getDatalakeDashboard).mockResolvedValue({ ...dashboard, totals: { ...dashboard.totals, reclaimable_bytes: 1024 ** 3 } });
    renderHome();
    expect(await screen.findByText("partial · 2 of 4 measured")).toBeInTheDocument();
    const impact = screen.getByRole("region", { name: "Storage impact" });
    expect(impact).toHaveTextContent("Estimate");
    expect(impact).toHaveTextContent("1 GB");
  });
  it("recovers a failed dashboard using Retry, while other cards remain usable", async () => {
    vi.mocked(getDatalakeDashboard).mockRejectedValue(new Error("dashboard down"));
    renderHome();
    expect(await screen.findByText("Dashboard unavailable")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Platform" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Storage impact" })).toHaveTextContent("Unavailable");
    vi.mocked(getDatalakeDashboard).mockResolvedValue(dashboard);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("link", { name: /tables at D or F/ })).toBeInTheDocument();
  });
  it("does not claim a healthy lake when grades are unavailable", async () => {
    vi.mocked(getDatalakeDashboard).mockResolvedValue({ ...dashboard, grade_distribution: undefined });
    renderHome();
    expect(await screen.findByText("Grade split unknown")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Tables at D/F unknown");
    expect(screen.queryByRole("link", { name: /tables at D or F/ })).not.toBeInTheDocument();
    expect(tablesAtGradeDf(undefined)).toBeNull();
    expect(tablesAtGradeDf([{ grade: "D", count: NaN }])).toBeNull();
  });
  it("shows approval errors instead of pretending there is nothing to review", async () => {
    vi.mocked(listPendingApprovals).mockRejectedValue(new Error("approvals down"));
    renderHome();
    expect(await screen.findByText(/Approval status unavailable/)).toBeInTheDocument();
    expect(screen.queryByText("No pending operation approvals")).not.toBeInTheDocument();
  });
  it("links approvals and scan queues to the right workspace", async () => {
    renderHome();
    expect(await screen.findByRole("link", { name: /1 operation approval awaiting review/ })).toHaveAttribute(
      "href",
      "/decisions?tab=approvals#operation-approvals"
    );
    expect(screen.getByRole("link", { name: /Scan queue: 0 waiting/ })).toHaveAttribute("href", "/runs/scanner");
  });
  it("refreshes read models without starting maintenance", async () => {
    renderHome();
    const refresh = await screen.findByRole("button", { name: "Refresh overview" });
    const before = vi.mocked(getDatalakeDashboard).mock.calls.length;
    fireEvent.click(refresh);
    await waitFor(() => expect(getDatalakeDashboard).toHaveBeenCalledTimes(before + 1));
    expect(screen.getByText("No recently discovered flags")).toBeInTheDocument();
  });
});
