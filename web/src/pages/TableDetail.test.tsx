import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getSilentDataLoss, repairSilentDataLoss, runMaintenance, triggerScan } from "../api/client";
import { TableDetail } from "./TableDetail";

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

vi.mock("../components/TableAiInsights", () => ({
  TableAiInsights: () => <div>Mock AI insights</div>
}));

vi.mock("../components/TableCompactionAnalytics", () => ({
  TableCompactionAnalytics: () => null
}));

vi.mock("../components/PlanPreview", () => ({
  PlanPreview: () => null
}));

vi.mock("../api/client", () => ({
  ApiError: class ApiError extends Error {
    status?: number;

    constructor(message: string, status?: number) {
      super(message);
      this.status = status;
    }
  },
  getTable: vi.fn().mockResolvedValue({
    id: "table-1",
    source_id: "source-1",
    schema_name: "public",
    table_name: "events",
    first_seen_at: "2026-07-01T00:00:00Z",
    is_active: true,
    stats: {
      grade: "B",
      overall_score: 82,
      total_size_gb: 12,
      total_files: 100,
      small_files_count: 20,
      cost_precision: "metadata",
      cost_last_full_scan_at: "2026-06-01T00:00:00Z",
      partition_col: "event_time",
      partition_col_type: "timestamp",
      raw: {
        column_quality: [
          {
            field_id: 1,
            column_name: "event_time",
            null_count: 0,
            value_count: 1000,
            null_rate: 0.0,
            lower_bound: "2026-07-01T00:00:00Z",
            upper_bound: "2026-07-01T23:59:59Z"
          },
          {
            field_id: 2,
            column_name: "user_email",
            null_count: 150,
            value_count: 1000,
            null_rate: 0.15,
            lower_bound: "aaa@example.com",
            upper_bound: "zzz@example.com"
          },
          {
            field_id: 3,
            column_name: "amount",
            null_count: 50,
            value_count: 1000,
            null_rate: 0.05,
            lower_bound: 1.0,
            upper_bound: 5000.0
          }
        ]
      }
    }
  }),
  getTablePartitions: vi.fn().mockResolvedValue([]),
  getTablePartitionAccess: vi.fn().mockResolvedValue({
    table_id: "table-1",
    since: "2026-05-21",
    points: [],
    partitions: []
  }),
  getTableAudit: vi.fn().mockResolvedValue([]),
  getTableHistory: vi.fn().mockResolvedValue([]),
  getBloomFilterRecommendations: vi.fn().mockResolvedValue({
    table_id: "table-1",
    table_name: "public.events",
    telemetry_source: "none",
    recommendations: []
  }),
  getTableHealthTrends: vi.fn().mockResolvedValue({
    table_id: "table-1",
    table_name: "events",
    current_grade: "B",
    current_score: 82,
    trend_points: []
  }),
  getEffectivePreset: vi.fn().mockResolvedValue({ preset_id: null, preset_name: null, provenance: "none" }),
  getTableCost: vi.fn().mockResolvedValue({
    table_id: "table-1",
    precision: "metadata",
    price_per_gb_month: 0.023,
    current_monthly_cost_usd: 10,
    projected_monthly_cost_usd: 8,
    projected_savings_usd_month: 2,
    query_request_savings_usd_month: 0,
    live_bytes: 1,
    dead_bytes: 0,
    metadata_bytes: 0,
    reclaimable_bytes: 0,
    by_operation: [],
    last_full_cost_scan_at: "2026-06-01T00:00:00Z"
  }),
  getTableS3Tiering: vi.fn().mockResolvedValue({
    table_id: "table-1",
    table_name: "public.events",
    current_size_gb: 12,
    avg_file_size_kb: 2560,
    days_since_last_access: 7,
    current_monthly_cost_usd: 10,
    intelligent_tiering_monthly_cost_usd: 8,
    estimated_annual_savings_usd: 24,
    recommendation: "KEEP_STANDARD",
    reason: "recently accessed"
  }),
  getPartitionEvolutionRecommendation: vi.fn().mockResolvedValue({
    table_id: "table-1",
    table_name: "events",
    current_partition_spec: "day(event_time)",
    recommended_partition_spec: "identity(customer_id), day(event_time)",
    reason: "Frequent equality predicates on customer_id suggest a mixed partitioning strategy.",
    confidence: 0.87,
    estimated_pruning_improvement_pct: 34,
    query_patterns_analyzed: 124,
    cardinality_estimate: "high",
    warnings: [],
    scan_ratio: null,
    avg_selectivity: null,
    trigger: "filter_frequency"
  }),
  getFastPathFindings: vi.fn().mockResolvedValue([]),
  getTableBenefitScores: vi.fn().mockResolvedValue({
    table_id: "table-1",
    partitions_scored: 1,
    top_candidates: []
  }),
  listPresets: vi.fn().mockResolvedValue([]),
  listDestinations: vi.fn().mockResolvedValue([
    {
      id: "dest-1",
      name: "trino",
      engine: "trino",
      config: {},
      enabled: true,
      created_at: "2026-07-01T00:00:00Z",
      updated_at: "2026-07-01T00:00:00Z"
    }
  ]),
  getComplianceStatus: vi.fn().mockResolvedValue({
    table_id: "table-1",
    table_ref: "iceberg.public.events",
    pending_deletion: null,
    abac_policy_count: 0,
    abac_enforcement_note: null
  }),
  getTableSignals: vi.fn().mockResolvedValue({
    table_id: "table-1",
    checked_at: "2026-07-01T00:00:00Z",
    signals: [],
    any_triggered: false
  }),
  listSources: vi.fn().mockResolvedValue([]),
  getTablePiiClassification: vi.fn().mockResolvedValue({
    table_id: "table-1",
    matches: [
      {
        column_name: "user_email",
        column_type: "string",
        pii_category: "EMAIL",
        similarity: 0.95,
        already_tagged: true
      }
    ]
  }),
  getDataQualityProfile: vi.fn().mockResolvedValue({
    table_id: "table-1",
    table_name: "events",
    profiled_at: "2026-07-01T00:00:00Z",
    column_metrics: [
      {
        column_name: "user_email",
        null_rate_pct: 15,
        null_rate_baseline_pct: 10,
        null_rate_spike: true,
        min_value: "aaa@example.com",
        max_value: "zzz@example.com",
        value_range_changed: false
      },
      {
        column_name: "amount",
        null_rate_pct: 5,
        null_rate_baseline_pct: 5,
        null_rate_spike: false,
        min_value: 1.0,
        max_value: 5000.0,
        value_range_changed: true,
        value_range_explanation: "Significant drop in minimum transaction amount detected"
      }
    ],
    anomalies_detected: 2,
    schema_version_changed: false,
    new_columns: [],
    dropped_columns: []
  }),
  assignTables: vi.fn(),
  unassignTables: vi.fn(),
  runMaintenance: vi.fn(),
  runManifestRewrite: vi.fn(),
  getTableRoiBreakdown: vi.fn().mockResolvedValue({
    table_id: "table-1",
    decision: {
      action: "run",
      reason: "benefit exceeds threshold",
      bcr: 2.1,
      threshold: 1.2,
      shadow: false,
      benefit_usd_month: 4.2,
      cost_usd_month: 2
    },
    monthly_benefit_usd: 4.2,
    monthly_cost_usd: 2,
    details: { io_savings_usd_month: 2.2, cpu_savings_usd_month: 2.0 }
  }),
  getSlaStatusDashboard: vi.fn().mockResolvedValue({
    generated_at: "2026-07-01T00:00:00Z",
    days: 7,
    total_tables: 1,
    healthy_tables: 1,
    warning_tables: 0,
    critical_tables: 0,
    buckets: []
  }),
  getQueryPerformanceDashboard: vi.fn().mockResolvedValue({
    summary: {
      telemetry_ready: false,
      total_queries: 0,
      p50_latency_ms: 0,
      p95_latency_ms: 0,
      p99_latency_ms: 0,
      avg_scanned_gb: 0,
      estimated_scan_cost_usd: 0
    },
    latency_trend: [],
    top_queries: []
  }),
  getCompactionCandidates: vi.fn().mockResolvedValue({
    generated_at: "2026-07-01T00:00:00Z",
    table_id: "table-1",
    table_name: "events",
    candidates: []
  }),
  getUnionable: vi.fn().mockResolvedValue({
    table_id: "table-1",
    table_name: "events",
    peers: [],
    generated_at: "2026-07-01T00:00:00Z"
  }),
  getColumnStats: vi.fn().mockResolvedValue({
    table_id: "table-1",
    columns: {}
  }),
  getSilentDataLoss: vi.fn().mockResolvedValue({
    table_id: "table-1",
    snapshot_id: null,
    orphan_data_files: [],
    orphan_size_bytes: 0,
    missing_files: [],
    detected_at: "2026-07-01T00:00:00Z",
    root_cause: "none",
    is_clean: true
  }),
  repairSilentDataLoss: vi.fn().mockResolvedValue({
    status: "accepted",
    message: "queued"
  }),
  patchTable: vi.fn().mockResolvedValue({
    id: "table-1",
    source_id: "source-1",
    schema_name: "public",
    table_name: "events",
    first_seen_at: "2026-07-01T00:00:00Z",
    is_active: true
  }),
  listTableAlertSubscriptions: vi.fn().mockResolvedValue([]),
  createTableAlertSubscription: vi.fn().mockResolvedValue({
    id: "sub-1",
    table_id: "table-1",
    email: "alerts@example.com"
  }),
  deleteTableAlertSubscription: vi.fn().mockResolvedValue(undefined),
  getRun: vi.fn().mockResolvedValue({
    id: "run-1",
    kind: "maintenance",
    status: "queued",
    created_at: "2026-07-01T00:00:00Z",
    trigger: "web"
  }),
  getRunJobs: vi.fn().mockResolvedValue([]),
  protectSnapshot: vi.fn(),
  triggerScan: vi.fn()
}));

const renderTableDetail = (initialEntry = "/tables/table-1") => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/tables/:tableId" element={<TableDetail />} />
          <Route path="/scans/:runId" element={<div>Scan run page</div>} />
          <Route path="/runs/:runId" element={<div>Maintenance run page</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
  return view;
};

describe("TableDetail page", () => {
  beforeEach(() => {
    vi.mocked(repairSilentDataLoss).mockClear();
    vi.mocked(triggerScan).mockResolvedValue({
      run_id: "scan-run-1",
      status: "accepted",
      scope: "tables",
      queued: true,
      message: "Scan queued"
    });
    vi.mocked(runMaintenance).mockResolvedValue({
      run_id: "maintenance-run-1",
      job_ids: ["job-1"],
      status: "queued"
    });
  });

  it("renders table detail and cost card", async () => {
    renderTableDetail();
    expect(await screen.findByText("public.events")).toBeInTheDocument();
    expect((await screen.findAllByText("Cost & Savings")).length).toBeGreaterThan(0);
    expect(await screen.findByText("Health Score")).toBeInTheDocument();
    expect(screen.getByText("Records")).not.toBeVisible();
    fireEvent.click(screen.getByText("More table metrics"));
    expect(await screen.findByText("Records")).toBeVisible();
  }, 15000);

  it("requires confirmation before running the silent-data-loss dry-run check, and never claims a repair/commit", async () => {
    vi.mocked(getSilentDataLoss).mockResolvedValueOnce({
      table_id: "table-1",
      snapshot_id: 42,
      orphan_data_files: ["s3://bucket/orphan-1.parquet", "s3://bucket/orphan-2.parquet"],
      orphan_size_bytes: 10485760,
      missing_files: [],
      detected_at: "2026-07-01T00:00:00Z",
      root_cause: "manifest drift",
      is_clean: false
    });

    renderTableDetail();

    // The action must be honestly labeled as a dry run -- it must never claim to repair,
    // rewrite manifests, commit, or delete anything, since the backend always runs with
    // commit=false from this UI.
    expect(screen.queryByRole("button", { name: /repair/i })).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "Run Spark dry-run check" }));
    expect(repairSilentDataLoss).not.toHaveBeenCalled();

    const dialog = screen.getByRole("alertdialog");
    expect(dialog).toHaveTextContent("dry run");
    expect(dialog).toHaveTextContent("does not rewrite manifests");
    expect(dialog).toHaveTextContent("does not delete or commit anything");
    expect(dialog).toHaveTextContent("2 suspected orphan data file(s)");
    expect(dialog).toHaveTextContent("10.0 MB");

    fireEvent.click(screen.getByRole("button", { name: "Run dry-run check" }));
    await waitFor(() => expect(repairSilentDataLoss).toHaveBeenCalledTimes(1));
  }, 15000);

  it("surfaces silent-data-loss evidence and known limitations in the alert body", async () => {
    vi.mocked(getSilentDataLoss).mockResolvedValueOnce({
      table_id: "table-1",
      snapshot_id: 42,
      orphan_data_files: ["s3://bucket/orphan-1.parquet"],
      orphan_size_bytes: 5242880,
      missing_files: [],
      detected_at: "2026-07-01T00:00:00Z",
      root_cause: "manifest drift",
      is_clean: false
    });

    renderTableDetail();

    expect(await screen.findByText("Silent data loss detected")).toBeInTheDocument();
    expect(screen.getByText(/1 orphan data file\(s\) totaling 5\.0 MB/)).toBeInTheDocument();
    expect(screen.getByText(/Root cause: manifest drift/)).toBeInTheDocument();
    expect(screen.getByText(/only checks the files Cryo already knows about/i)).toBeInTheDocument();
  }, 15000);

  it("shows an honest not-found state with recovery links when the table can't be loaded", async () => {
    const { getTable } = await import("../api/client");
    vi.mocked(getTable).mockResolvedValueOnce(undefined as never);

    renderTableDetail();

    expect(await screen.findByText("Table not found")).toBeInTheDocument();
    expect(screen.getByText("This table couldn't be found")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Search catalogs" })).toHaveAttribute("href", "/catalogs");
    expect(screen.getByRole("link", { name: "Go to Datalake health" })).toHaveAttribute("href", "/");
  }, 15000);

  it("defaults to the Overview tab, keeps the active tab and URL in sync when clicking a tab", async () => {
    renderTableDetail();
    expect(await screen.findByText("public.events")).toBeInTheDocument();

    const overviewTab = screen.getByRole("tab", { name: /Overview/ });
    expect(overviewTab).toHaveAttribute("aria-selected", "true");

    const structureTab = screen.getByRole("tab", { name: /Structure/ });
    // Radix's Tabs selects on mousedown/focus (automatic activation mode), not on the
    // synthetic "click" event alone.
    fireEvent.mouseDown(structureTab);

    await waitFor(() => expect(structureTab).toHaveAttribute("aria-selected", "true"));
    expect(overviewTab).toHaveAttribute("aria-selected", "false");
  }, 15000);

  it("restores the active tab from the ?tab= URL param on load", async () => {
    renderTableDetail("/tables/table-1?tab=cost");
    expect(await screen.findByText("public.events")).toBeInTheDocument();

    const costTab = await screen.findByRole("tab", { name: /Cost/ });
    await waitFor(() => expect(costTab).toHaveAttribute("aria-selected", "true"));
    expect(screen.getByRole("tab", { name: /Overview/ })).toHaveAttribute("aria-selected", "false");
  }, 15000);

  it("queues scan without redirecting until follow-up click", async () => {
    renderTableDetail();
    expect(await screen.findByText("public.events")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Refresh stats" }));

    await waitFor(() => {
      expect(triggerScan).toHaveBeenCalled();
    });
    expect(screen.queryByText("Scan run page")).not.toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: "Open scan run" }));
    expect(await screen.findByText("Scan run page")).toBeInTheDocument();
  }, 15000);

  it("disables Run maintenance when no preset is assigned", async () => {
    renderTableDetail();
    expect(await screen.findByText("public.events")).toBeInTheDocument();

    const runButton = screen.getByRole("button", { name: "Run maintenance" });
    expect(runButton).toBeDisabled();
  }, 15000);

  it("queues maintenance run when a preset is assigned", async () => {
    const { getEffectivePreset, listPresets } = await import("../api/client");
    vi.mocked(getEffectivePreset).mockResolvedValue({
      table_id: "table-1",
      preset_id: "preset-1",
      preset_name: "local-standard",
      provenance: "assignment"
    });
    vi.mocked(listPresets).mockResolvedValue([
      {
        id: "preset-1",
        name: "local-standard",
        destination_id: "dest-1",
        mode: "standard",
        operations: ["optimize"],
        params: {},
        priority: 100,
        enabled: true,
        guardrails: {},
        created_at: "2026-07-01T00:00:00Z",
        updated_at: "2026-07-01T00:00:00Z"
      }
    ]);

    renderTableDetail();
    expect(await screen.findByText("public.events")).toBeInTheDocument();

    const runButton = await screen.findByRole("button", { name: "Run maintenance" });
    expect(runButton).not.toBeDisabled();
    fireEvent.click(runButton);
    fireEvent.click(await screen.findByRole("button", { name: "Confirm" }));

    await waitFor(() => {
      expect(runMaintenance).toHaveBeenCalledWith(
        expect.anything(),
        expect.objectContaining({
          table_ids: ["table-1"],
          destination_id: "dest-1",
          preset_id: "preset-1"
        })
      );
    });
    expect(screen.queryByText("Maintenance run page")).not.toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: "Open maintenance run" }));
    expect(await screen.findByText("Maintenance run page")).toBeInTheDocument();
  }, 15000);

  it("renders active health signals, diagnostic matrix, preset selectors, fix buttons, and red notification badges", async () => {
    const { getTableSignals } = await import("../api/client");
    vi.mocked(getTableSignals).mockResolvedValueOnce({
      table_id: "table-1",
      checked_at: "2026-07-01T00:00:00Z",
      signals: [
        {
          signal_name: "file_degradation",
          severity: "critical",
          triggered: true,
          reason: "Excessive small files detected",
          metric_value: 0.2,
          threshold: 0.1
        }
      ],
      any_triggered: true
    });

    renderTableDetail();

    expect(await screen.findByText("public.events")).toBeInTheDocument();

    // Overview is the default tab and now contains the consolidated Act Now + diagnostics content.
    expect(await screen.findByText("Overview")).toBeInTheDocument();

    // The Act Now card is the single deduplicated rendering of this signal -- it used to also
    // render a second time in a separate "Detected Health Issues" panel; that duplication was
    // removed, so each triggered signal now appears exactly once.
    expect(await screen.findByText("Small-File Fragmentation Pressure")).toBeInTheDocument();

    // Verify current state data from stats is rendered
    expect(await screen.findByText("20.0% small files (20 / 100 total files)")).toBeInTheDocument();
    expect(await screen.findByText("< 20.0% small files ratio")).toBeInTheDocument();

    // Preset assignment has one consistent home in the global action strip.
    expect(await screen.findByRole("combobox", { name: "Assign preset" })).toBeInTheDocument();
    expect(screen.getAllByRole("combobox", { name: "Assign preset" })).toHaveLength(1);
    expect(await screen.findByRole("button", { name: "Act now" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Act now" })).toHaveLength(1);
  }, 15000);

  it("renders the Partition Columns Analyzer with Check 1 and Check 2 validations", async () => {
    renderTableDetail();

    expect(await screen.findByText("public.events")).toBeInTheDocument();

    fireEvent.click(await screen.findByText("Diagnostics"));

    // Verify Partition Columns Analyzer card headers and descriptions
    expect(await screen.findByText("Partition Columns Analyzer")).toBeInTheDocument();
    expect(await screen.findByText("Analyzing schema keys, access patterns, and pruning granularity")).toBeInTheDocument();

    // Verify summary statistics grid
    expect(await screen.findByText("Active Partition Column")).toBeInTheDocument();
    expect((await screen.findAllByText("event_time")).length).toBeGreaterThan(0);

    // Verify Check 1 and Check 2 titles
    expect(await screen.findByText("Check 1: Target Column Alignment")).toBeInTheDocument();
    expect(await screen.findByText("Check 2: Partition Granularity Validation")).toBeInTheDocument();

    // Verify the mock recommended optimal spec blue tag is rendered
    expect((await screen.findAllByText("identity(customer_id), day(event_time)")).length).toBeGreaterThan(0);
  }, 15000);
});
