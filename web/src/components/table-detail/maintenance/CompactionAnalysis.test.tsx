import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { tableKeys } from "../../../pages/table-detail/tableKeys";
import {
  AUTOCOMP_DEFINITION,
  CompactionAnalysis,
  deleteFileHealthLabel,
  HORIZON_SCORE_BASIS,
  STORED_SCORE_BASIS
} from "./CompactionAnalysis";

const client = vi.hoisted(() => ({
  getAutoCompCandidates: vi.fn(),
  getCompactionCandidates: vi.fn(),
  getTableSignals: vi.fn(),
  getTable: vi.fn(),
  updateTableBenefitScores: vi.fn().mockResolvedValue({})
}));
const writeAccess = vi.hoisted(() => ({ allowed: false, reason: "viewer" as string | null, hidden: false }));

vi.mock("../../../api/client", () => client);
vi.mock("../../../lib/auth", () => ({
  useAuth: () => ({ api: { token: "test-token" }, token: "test-token", role: "viewer" })
}));
vi.mock("../../../lib/useWriteControl", () => ({
  useWriteControl: (action: string) => {
    expect(action).toBe("runMaintenance");
    return writeAccess;
  }
}));

const renderAnalysis = () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <CompactionAnalysis tableId="table-1" />
    </QueryClientProvider>
  );
  return { queryClient, ...view };
};

describe("CompactionAnalysis", () => {
  beforeEach(() => {
    writeAccess.allowed = false;
    client.getAutoCompCandidates.mockResolvedValue({ items: [], skipped: [], compare: {} });
    client.getCompactionCandidates.mockResolvedValue({ candidates: [] });
    client.getTableSignals.mockResolvedValue({ signals: [], any_triggered: false, checked_at: "2026-09-28T12:00:00Z" });
    client.getTable.mockResolvedValue(null);
    client.updateTableBenefitScores.mockClear();
  });

  it("keeps both rankings and defines AutoComp once", async () => {
    renderAnalysis();
    expect(await screen.findByText(STORED_SCORE_BASIS)).toBeInTheDocument();
    expect(screen.getByText(HORIZON_SCORE_BASIS)).toBeInTheDocument();
    expect(screen.getAllByText(AUTOCOMP_DEFINITION)).toHaveLength(1);
    expect(screen.getByRole("heading", { name: "Stored benefit-score candidates" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Horizon query-frequency candidates" })).toBeInTheDocument();
    expect(document.getElementById("maintenance-compaction")).toBeTruthy();
    expect(screen.queryByText("optimize")).not.toBeInTheDocument();
    expect(screen.queryByText("0 GB")).not.toBeInTheDocument();
    expect(screen.queryByText(/^0$/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh benefit scores" })).toBeDisabled();
  });

  it("fetches table stats on the shared table key and refreshes when allowed", async () => {
    writeAccess.allowed = true;
    writeAccess.reason = null;
    const { queryClient } = renderAnalysis();
    await waitFor(() => {
      expect(client.getTable).toHaveBeenCalledWith({ token: "test-token" }, "table-1");
    });
    const keys = queryClient.getQueryCache().getAll().map((query) => query.queryKey);
    expect(keys).toContainEqual(tableKeys.detail("table-1", "test-token"));
    expect(keys).toContainEqual(tableKeys.compactionCandidates("table-1", "test-token"));
    expect(keys).toContainEqual(tableKeys.signals("table-1", "test-token"));
    expect(keys.some((key) => Array.isArray(key) && key[0] === "table-compaction-stats")).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Refresh benefit scores" }));
    await waitFor(() => {
      expect(client.updateTableBenefitScores).toHaveBeenCalledWith({ token: "test-token" }, "table-1");
    });
  });

  it("does not treat missing delete-file stats as healthy", () => {
    expect(deleteFileHealthLabel(null)).toBe("not reported");
    expect(deleteFileHealthLabel({ delete_file_pct: 12 })).toBe("Major rewrite recommended");
  });
});
