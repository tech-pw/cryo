import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TableCompactionAnalytics } from "./TableCompactionAnalytics";

vi.mock("../lib/auth", () => ({
  useAuth: () => ({
    api: { token: "test-token" },
    token: "test-token",
    role: "viewer",
    subject: "tester",
    loading: false,
    error: null,
    isAuthenticated: true,
    signIn: vi.fn(),
    signOut: vi.fn()
  })
}));

const getAutoCompCandidates = vi.fn();
const getTable = vi.fn();
const getTableSignals = vi.fn();
const updateTableBenefitScores = vi.fn();

vi.mock("../api/client", () => ({
  getAutoCompCandidates: (...args: unknown[]) => getAutoCompCandidates(...args),
  getTable: (...args: unknown[]) => getTable(...args),
  getTableSignals: (...args: unknown[]) => getTableSignals(...args),
  updateTableBenefitScores: (...args: unknown[]) => updateTableBenefitScores(...args)
}));

function renderWithClient(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } }
  });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

describe("TableCompactionAnalytics", () => {
  it("renders MOOP scores and skip reasons from the autocomp candidates endpoint", async () => {
    getAutoCompCandidates.mockResolvedValue({
      table_id: "table-1",
      items: [
        {
          partition_value: "2025-05-02",
          small_files: 100,
          size_gb: 10,
          query_frequency: 5,
          benefit_score: 50,
          moop_score: 0.9,
          eligible: true,
          skip_reason: null
        },
        {
          partition_value: "2025-05-03",
          small_files: 1,
          size_gb: 1,
          query_frequency: 0,
          benefit_score: 0.1,
          moop_score: -0.1,
          eligible: false,
          skip_reason: "below_min_benefit"
        }
      ],
      skipped: [{ partition_value: "2025-05-03", skip_reason: "below_min_benefit" }],
      effectiveness: { effectiveness_ratio: 42, effectiveness: "good", run_count: 3 },
      compare: { autocomp_order: [], static_order: [], improvement_ratio: 1.3, autocomp_wins: true }
    });
    getTable.mockResolvedValue({
      id: "table-1",
      source_id: "source-1",
      schema_name: "analytics",
      table_name: "events",
      first_seen_at: "2026-01-01T00:00:00Z",
      is_active: true,
      stats: {
        position_delete_files: 80,
        delete_file_pct: 3.0,
        raw: { format_version: 2 }
      }
    });
    getTableSignals.mockResolvedValue({ checked_at: "2026-07-20T00:00:00Z", any_triggered: false, signals: [] });

    renderWithClient(<TableCompactionAnalytics tableId="table-1" />);

    await waitFor(() => expect(screen.getByText("0.900")).toBeInTheDocument());
    expect(screen.getByText("below_min_benefit")).toBeInTheDocument();
    expect(screen.getByText(/AutoComp ranking beats static/)).toBeInTheDocument();
    // 80 position-delete files >= the 50-file minor threshold, 3.0% delete_file_pct is below
    // the 10% major threshold -> minor compaction recommendation, not "Healthy" or "Major".
    expect(screen.getByText("Minor delete compaction recommended")).toBeInTheDocument();
  });

  it("shows a healthy delete-file chip when well under both thresholds", async () => {
    getAutoCompCandidates.mockResolvedValue({
      table_id: "table-1",
      items: [],
      skipped: [],
      effectiveness: null,
      compare: { autocomp_order: [], static_order: [], improvement_ratio: 0, autocomp_wins: false }
    });
    getTable.mockResolvedValue({
      id: "table-1",
      source_id: "source-1",
      schema_name: "analytics",
      table_name: "events",
      first_seen_at: "2026-01-01T00:00:00Z",
      is_active: true,
      stats: { position_delete_files: 2, delete_file_pct: 0.1, raw: { format_version: 2 } }
    });
    getTableSignals.mockResolvedValue({ checked_at: "2026-07-20T00:00:00Z", any_triggered: false, signals: [] });

    renderWithClient(<TableCompactionAnalytics tableId="table-1" />);

    await waitFor(() => expect(screen.getByText("Healthy")).toBeInTheDocument());
  });

  it("shows the V3 deletion-vector chip instead of the V2 file-count policy", async () => {
    getAutoCompCandidates.mockResolvedValue({
      table_id: "table-1",
      items: [],
      skipped: [],
      effectiveness: null,
      compare: { autocomp_order: [], static_order: [], improvement_ratio: 0, autocomp_wins: false }
    });
    getTable.mockResolvedValue({
      id: "table-1",
      source_id: "source-1",
      schema_name: "analytics",
      table_name: "events",
      first_seen_at: "2026-01-01T00:00:00Z",
      is_active: true,
      // Would trigger "major" under the V2 policy if format_version were ignored.
      stats: { position_delete_files: 500, delete_file_pct: 50.0, raw: { format_version: 3 } }
    });
    getTableSignals.mockResolvedValue({ checked_at: "2026-07-20T00:00:00Z", any_triggered: false, signals: [] });

    renderWithClient(<TableCompactionAnalytics tableId="table-1" />);

    await waitFor(() => expect(screen.getByText("V3 (deletion vectors)")).toBeInTheDocument());
  });
});
