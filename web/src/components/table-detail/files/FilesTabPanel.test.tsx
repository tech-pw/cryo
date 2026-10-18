import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { FilesTabPanel } from "./FilesTabPanel";

vi.mock("../../../lib/auth", () => ({
  useAuth: () => ({
    api: { token: "test-token" },
    token: "test-token",
    role: "viewer"
  })
}));

vi.mock("../PropertyRecommendations", () => ({
  PropertyRecommendations: () => <div>Property recommendations</div>
}));

vi.mock("../WairRecluster", () => ({
  WairRecluster: () => <div>Workload-aware reclustering</div>
}));

const getTable = vi.fn();
const getTablePartitions = vi.fn();
const getTableHistory = vi.fn();
const getTablePiiClassification = vi.fn();
const getDataQualityProfile = vi.fn();
const getColumnStats = vi.fn();
const getBloomFilterRecommendations = vi.fn();
const getPropertyRecommendations = vi.fn();

vi.mock("../../../api/client", () => ({
  getTable: (...args: unknown[]) => getTable(...args),
  getTablePartitions: (...args: unknown[]) => getTablePartitions(...args),
  getTableHistory: (...args: unknown[]) => getTableHistory(...args),
  getTablePiiClassification: (...args: unknown[]) => getTablePiiClassification(...args),
  getDataQualityProfile: (...args: unknown[]) => getDataQualityProfile(...args),
  getColumnStats: (...args: unknown[]) => getColumnStats(...args),
  getBloomFilterRecommendations: (...args: unknown[]) => getBloomFilterRecommendations(...args),
  getPropertyRecommendations: (...args: unknown[]) => getPropertyRecommendations(...args)
}));

const tableShell = (stats: Record<string, unknown>) => ({
  id: "table-1",
  source_id: "source-1",
  schema_name: "public",
  table_name: "events",
  first_seen_at: "2026-09-01T00:00:00Z",
  is_active: true,
  stats
});

function renderPanel(search = "") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } }
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/tables/table-1${search}`]}>
        <Routes>
          <Route path="/tables/:tableId" element={<FilesTabPanel />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("FilesTabPanel", () => {
  beforeEach(() => {
    getTable.mockReset();
    getTablePartitions.mockReset();
    getTableHistory.mockReset();
    getTablePiiClassification.mockReset();
    getDataQualityProfile.mockReset();
    getColumnStats.mockReset();
    getBloomFilterRecommendations.mockReset();
    getPropertyRecommendations.mockReset();
    getTablePartitions.mockResolvedValue([]);
    getTableHistory.mockResolvedValue([]);
    getTablePiiClassification.mockResolvedValue({ table_id: "table-1", matches: [] });
    getDataQualityProfile.mockResolvedValue({
      table_id: "table-1",
      table_name: "events",
      profiled_at: "2026-09-01T00:00:00Z",
      column_metrics: [],
      anomalies_detected: 0,
      schema_version_changed: false,
      new_columns: [],
      dropped_columns: []
    });
    getColumnStats.mockResolvedValue({ table_id: "table-1", columns: {} });
    getBloomFilterRecommendations.mockResolvedValue({
      table_id: "table-1",
      table_name: "events",
      telemetry_source: "none",
      recommendations: []
    });
    getPropertyRecommendations.mockResolvedValue({
      table_id: "table-1",
      generated_at: "2026-09-01T00:00:00Z",
      items: [
        {
          property_key: "write.target-file-size-bytes",
          current_value: "268435456",
          recommended_value: "536870912",
          reason: "size",
          tier: "tier1",
          action: "apply",
          layout_recommendation_id: null,
          bloom_filter_column: null,
          needs_confirmation: false
        }
      ],
      gc_blocked: false
    });
  });

  it("renders one line and no chart for an unpartitioned table", async () => {
    getTable.mockResolvedValue(
      tableShell({
        partition_col: "_unpartitioned",
        raw: { column_quality: [] }
      })
    );
    getTablePartitions.mockResolvedValue([
      {
        id: 1,
        partition_value: "_unpartitioned",
        files_per_partition: 8,
        small_files_in_partition_count: 3
      }
    ]);

    renderPanel();

    expect(await screen.findByText("Unpartitioned table")).toBeInTheDocument();
    expect(screen.queryByText(/Top \d+ of \d+ partitions/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("chart-plot")).not.toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "Small-file rate" })).not.toBeInTheDocument();
    expect(screen.queryByText("Optimal")).not.toBeInTheDocument();
    const files = document.getElementById("files-files");
    expect(files).not.toBeNull();
    expect(within(files as HTMLElement).getByText("Data files").parentElement).toHaveTextContent("Not measured");
    expect(document.getElementById("files-properties")).toBeNull();
    expect(screen.getByRole("link", { name: "Config" })).toHaveAttribute("href", "/tables/table-1?tab=config#config-properties");
    expect(screen.getByRole("heading", { name: "Layout analysis (WAIR: workload-aware reclustering)" })).toBeInTheDocument();
  });

  it("reads a null null rate as Not measured and sorts it after measured columns", async () => {
    getTable.mockResolvedValue(
      tableShell({
        partition_col: "event_ts",
        raw: {
          column_quality: [
            { column_name: "aaa", column_type: "string", null_rate: null },
            { column_name: "zzz", column_type: "bigint", null_rate: 0.2 }
          ]
        }
      })
    );

    renderPanel("?column=zzz");

    expect(await screen.findByText("20.0% Null")).toBeInTheDocument();
    expect(screen.getAllByText("Not measured").length).toBeGreaterThan(0);
    expect(screen.queryByText("0.0% Null")).not.toBeInTheDocument();
    const names = screen.getAllByTestId("schema-column-name").map((node) => node.textContent);
    expect(names.indexOf("zzz")).toBeGreaterThanOrEqual(0);
    expect(names.indexOf("zzz")).toBeLessThan(names.indexOf("aaa"));
    expect(document.getElementById("files-schema-highlight")).toHaveTextContent("zzz");
  });

  it("lists scan points with gaps; the series is the small-file count", async () => {
    getTable.mockResolvedValue(
      tableShell({
        partition_col: "event_ts",
        total_files: 10,
        small_files_count: 2,
        raw: { column_quality: [] }
      })
    );
    getTableHistory.mockResolvedValue([
      { scan_at: "2026-09-01T00:00:00Z", small_files_count: 12, total_size_gb: 99.5 },
      { scan_at: "2026-09-02T00:00:00Z", small_files_count: null, total_size_gb: 99.5 }
    ]);

    renderPanel();

    const series = await screen.findByLabelText("Small-file candidates over scans");
    expect(series).toHaveAttribute("data-series", "small-file count");
    const plot = await within(series).findByTestId("timeseries-plot");
    expect(plot).toHaveAttribute("data-connect-nulls", "false");
    expect(plot).toHaveAttribute("data-plotted-values", "12");
    expect(within(series).getByTestId("timeseries-gap")).toBeInTheDocument();
    expect(within(series).queryByText(/99\.5/)).not.toBeInTheDocument();
    expect(screen.queryByText("Size (GB)")).not.toBeInTheDocument();
  });
});
