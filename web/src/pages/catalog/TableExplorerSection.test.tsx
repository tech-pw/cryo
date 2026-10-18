import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeProvider } from "../../lib/themeContext";
import {
  EMPTY_TABLES_TITLE,
  clearFilterParams,
  explorerPredicate,
  filtersFromSearchParams,
  filtersToSearchParamEntries,
  searchParamsForSavedView
} from "../../lib/tableFilters";
import { SlaBulletBar, SmallFileRatio, TableExplorerSection } from "./TableExplorerSection";

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
  assignTables: vi.fn(),
  exportTablesCsv: vi.fn(),
  groupTables: vi.fn(),
  deleteExplorerView: vi.fn(),
  listDestinations: vi.fn().mockResolvedValue([]),
  listExplorerViews: vi.fn().mockResolvedValue([]),
  listPresets: vi.fn().mockResolvedValue([]),
  listSources: vi.fn().mockResolvedValue([]),
  listTables: vi.fn(),
  saveExplorerView: vi.fn(),
  translateTableFilter: vi.fn(),
  runMaintenance: vi.fn(),
  runManifestRewrite: vi.fn(),
  triggerScan: vi.fn(),
  unassignTables: vi.fn()
}));

import { listTables } from "../../api/client";

const LocationProbe = () => {
  const location = useLocation();
  return <div data-testid="location-probe">{`${location.pathname}${location.search}`}</div>;
};

const renderExplorer = (initialRoute: string) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } }
  });
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <MemoryRouter initialEntries={[initialRoute]}>
          <Routes>
            <Route
              path="*"
              element={
                <>
                  <TableExplorerSection />
                  <LocationProbe />
                </>
              }
            />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>
  );
};

describe("SmallFileRatio", () => {
  it("shows small files over total files and the ratio", () => {
    render(<SmallFileRatio smallFiles={12} totalFiles={40} />);
    expect(screen.getByText("30%")).toBeInTheDocument();
    expect(screen.getByText("12 / 40")).toBeInTheDocument();
    expect(screen.getByTitle("12 / 40 files (30%)")).toBeInTheDocument();
  });

  it("does not turn unknown file counts into 0", () => {
    const { rerender } = render(<SmallFileRatio smallFiles={null} totalFiles={40} />);
    expect(screen.getByText("Not measured")).toBeInTheDocument();
    expect(screen.queryByText(/0 \/ 40/)).not.toBeInTheDocument();

    rerender(<SmallFileRatio smallFiles={12} totalFiles={undefined} />);
    expect(screen.getByText("Not measured")).toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });
});

describe("SlaBulletBar", () => {
  it("renders Unknown when either side is null", () => {
    const { rerender } = render(<SlaBulletBar label="Freshness" actual={null} threshold={24} />);
    expect(screen.getByLabelText("Freshness")).toHaveTextContent("Unknown");
    expect(screen.queryByRole("presentation")).not.toBeInTheDocument();
    expect(document.querySelector("[data-bullet-bar]")).not.toBeInTheDocument();

    rerender(<SlaBulletBar label="Freshness" actual={40} threshold={null} />);
    expect(screen.getByLabelText("Freshness")).toHaveTextContent("Unknown");
  });

  it("renders a bar when both sides are known", () => {
    render(<SlaBulletBar label="Freshness" actual={12} threshold={24} />);
    expect(screen.getByLabelText("Freshness")).toHaveTextContent("12 / 24");
    expect(document.querySelector("[data-bullet-bar]")).toHaveStyle({ width: "50%" });
  });
});

describe("table filter URL", () => {
  it("parses ?grade=F&needs=compaction", () => {
    const filters = filtersFromSearchParams(new URLSearchParams("grade=F&needs=compaction"));
    expect(filters.grades).toEqual(["F"]);
    expect(filters.needsCompaction).toBe(true);
  });

  it("reads old keys as aliases of the spec keys", () => {
    const filters = filtersFromSearchParams(
      new URLSearchParams("grades=D,F&needsCompaction=1&source=src-1&minSizeGb=10&minSmallFiles=4&minReclaimableGb=2")
    );
    expect(filters.grades).toEqual(["D", "F"]);
    expect(filters.needsCompaction).toBe(true);
    expect(filters.sourceIds).toEqual(["src-1"]);
    expect(filters.minSizeGb).toBe(10);
    expect(filters.minSmallFiles).toBe(4);
    expect(filters.minReclaimableGb).toBe(2);
  });

  it("writes spec keys and drops aliases when filters are cleared", () => {
    const filters = filtersFromSearchParams(new URLSearchParams("grade=F&needs=compaction"));
    const written = new URLSearchParams(filtersToSearchParamEntries(filters));
    expect(written.get("grade")).toBe("F");
    expect(written.get("needs")).toBe("compaction");
    expect(written.has("grades")).toBe(false);
    expect(written.has("needsCompaction")).toBe(false);

    const cleared = clearFilterParams(
      new URLSearchParams("grade=F&needs=compaction&grades=D&needsCompaction=1&page=2&sort=-grade")
    );
    expect(cleared.has("grade")).toBe(false);
    expect(cleared.has("needs")).toBe(false);
    expect(cleared.has("grades")).toBe(false);
    expect(cleared.has("needsCompaction")).toBe(false);
    expect(cleared.has("page")).toBe(false);
    expect(cleared.get("sort")).toBe("-grade");
  });

  it("keeps grade and compaction on the server predicate", () => {
    const filters = filtersFromSearchParams(new URLSearchParams("grade=F&needs=compaction&schema=analytics"));
    const predicate = explorerPredicate(filters, { schema: "analytics", view: null });
    const serialized = JSON.stringify(predicate);
    expect(serialized).toContain('"field":"grade"');
    expect(serialized).toContain("F");
    expect(serialized).toContain('"field":"needs_compaction"');
    expect(serialized).toContain('"field":"schema_name"');
    expect(serialized).toContain("analytics");
  });

  it("does not invent a predicate for views the table list cannot filter", () => {
    const filters = filtersFromSearchParams(new URLSearchParams("view=freshness"));
    const predicate = explorerPredicate(filters, { view: "freshness" });
    const serialized = JSON.stringify(predicate);
    expect(serialized).not.toContain("p95");
    expect(serialized).not.toContain("maintenance_days");
    expect(serialized).toContain("is_active");
  });

  it("writes canonical URL keys for a named view", () => {
    const params = searchParamsForSavedView({
      filters: { activeOnly: true, grades: ["D", "F"], needsCompaction: true },
      sort: "-small_files_count",
      view: "scan-failed"
    });
    expect(params.get("grade")).toBe("D,F");
    expect(params.get("needs")).toBe("compaction");
    expect(params.get("view")).toBe("scan-failed");
    expect(params.has("grades")).toBe(false);
    expect(params.has("needsCompaction")).toBe(false);
  });
});

describe("TableExplorerSection filters", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.mocked(listTables).mockReset();
    vi.mocked(listTables).mockResolvedValue({
      items: [],
      total: 0,
      limit: 50,
      offset: 0
    });
  });

  it("shows the empty-filter sentence and clears grade and needs from the URL", async () => {
    renderExplorer("/tables?grade=F&needs=compaction");

    expect(await screen.findByRole("heading", { name: EMPTY_TABLES_TITLE })).toBeInTheDocument();
    expect(screen.queryByText(/fleet is healthy/i)).not.toBeInTheDocument();
    expect(screen.queryByText("No tables match this view")).not.toBeInTheDocument();

    await waitFor(() => expect(listTables).toHaveBeenCalled());
    const call = vi.mocked(listTables).mock.calls.at(-1);
    expect(call?.[1]).toEqual(
      expect.objectContaining({
        limit: 50,
        offset: 0
      })
    );
    expect(JSON.stringify(call?.[1].predicate)).toContain("needs_compaction");
    expect(call?.[1].limit).not.toBeGreaterThan(50);

    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));

    await waitFor(() => {
      const location = screen.getByTestId("location-probe").textContent ?? "";
      expect(location).not.toContain("grade=");
      expect(location).not.toContain("needs=");
      expect(location).not.toContain("grades=");
      expect(location).not.toContain("needsCompaction=");
    });
  });

  it.each([
    ["/tables?view=freshness", { freshness_breach: true }],
    ["/tables?view=maintenance-age", { maintenance_overdue: true }],
    ["/tables?view=p95-latency", { p95_breach: true }],
    ["/tables?view=unused", { no_reads_days: 30 }],
    ["/tables?view=unused&window=7", { no_reads_days: 7 }]
  ])("sends the view filter spec for %s", async (route, spec) => {
    renderExplorer(route);
    await waitFor(() => expect(listTables).toHaveBeenCalled());
    expect(vi.mocked(listTables).mock.calls.at(-1)?.[1]).toEqual(expect.objectContaining(spec));
    expect(screen.queryByText(/is not applied/i)).not.toBeInTheDocument();
  });

  it("does not describe a coverage gap as unused", async () => {
    vi.mocked(listTables).mockResolvedValue({
      items: [],
      total: 0,
      limit: 50,
      offset: 0,
      telemetry_coverage: 0,
      telemetry_gap_tables: 4,
      telemetry_window_days: 30,
      telemetry_source: "none"
    });
    renderExplorer("/tables?view=unused");
    expect(await screen.findByText(/not unused/i)).toBeInTheDocument();
    expect(screen.queryByText(/is not applied/i)).not.toBeInTheDocument();
  });

  it("links a row to /tables/:id and keeps unknown file counts unmeasured", async () => {
    vi.mocked(listTables).mockResolvedValue({
      items: [
        {
          id: "tbl-1",
          source_id: "src-1",
          schema_name: "analytics",
          table_name: "orders",
          catalog_name: "prod",
          first_seen_at: "2026-01-01T00:00:00Z",
          is_active: true,
          stats: {
            grade: "F",
            total_files: null,
            small_files_count: null,
            total_size_gb: 2
          }
        }
      ],
      total: 1,
      limit: 50,
      offset: 0
    } as never);

    renderExplorer("/tables");

    const link = await screen.findByRole("link", { name: "analytics.orders" });
    expect(link).toHaveAttribute("href", "/tables/tbl-1");
    const table = link.closest("table");
    expect(table).toBeInTheDocument();
    // This used to assert table-auto/w-full and that no th carried an inline style. Both
    // became wrong when the catalog table gained resizable columns (e5414f17): the layout
    // moved to table-fixed and each th now needs an explicit width for resizing to work.
    // The test kept asserting the pre-feature markup and left main red, so it now checks
    // the rendering behaviour the feature actually guarantees -- every column sized, which
    // is what table-fixed requires -- rather than the classes it happens to use.
    const headers = Array.from(table?.querySelectorAll("th") ?? []);
    expect(headers.length).toBeGreaterThan(0);
    expect(headers.every((th) => th.style.width !== "")).toBe(true);
    expect(screen.getAllByText("Not measured").length).toBeGreaterThan(0);
    expect(screen.queryByText(/0 \/ /)).not.toBeInTheDocument();
  });

  it("shows each table's maintenance status, reason and last run instead of folded subtitle facts", async () => {
    vi.mocked(listTables).mockResolvedValue({
      items: [
        {
          id: "tbl-2",
          source_id: "src-1",
          schema_name: "sales",
          table_name: "events",
          first_seen_at: "2026-01-01T00:00:00Z",
          is_active: true,
          consecutive_scan_failures: 0,
          stats: { grade: "C", overall_score: 61.6, total_files: 100, small_files_count: 40, total_size_gb: 5 },
          maintenance: {
            state: "skipped",
            reason: "Cooldown after a recent run",
            workflow_pinned: false,
            score_delta_7d: -12,
            last_run: {
              run_id: "run-9",
              job_id: "job-9",
              status: "succeeded",
              trigger: "schedule",
              finished_at: "2026-10-17T02:00:00Z"
            }
          }
        }
      ],
      total: 1,
      limit: 50,
      offset: 0
    } as never);

    renderExplorer("/tables");

    expect(await screen.findByText("Skipped")).toBeInTheDocument();
    expect(screen.getAllByText("Cooldown after a recent run").length).toBeGreaterThan(0);
    expect(screen.getByText("62")).toBeInTheDocument();
    expect(screen.getByTitle("Change in health score over the last 7 days")).toHaveTextContent("▼12");
    expect(screen.getByRole("link", { name: /succeeded · nightly/i })).toHaveAttribute("href", "/runs/run-9");
    expect(screen.queryByText(/Scan failures not measured/)).not.toBeInTheDocument();
  });

  it("switches quick views through the URL", async () => {
    renderExplorer("/tables");
    fireEvent.click(await screen.findByRole("button", { name: /Failed last run/ }));
    await waitFor(() => expect(screen.getByTestId("location-probe").textContent).toContain("view=failed"));
    fireEvent.click(screen.getByRole("button", { name: /^Savings/ }));
    await waitFor(() => {
      const location = screen.getByTestId("location-probe").textContent ?? "";
      expect(location).toContain("view=savings");
      expect(location).toContain("sort=-projected_savings_usd_month");
    });
  });
});
