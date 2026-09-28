import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";
import { ActNowSection } from "./ActNowSection";
import { PropertyRecommendations } from "./PropertyRecommendations";
import { QualityConstraints } from "./QualityConstraints";
import { WairRecluster } from "./WairRecluster";

const viewerReason = "Viewer access is read-only. Operator or admin role is required to change this.";

vi.mock("../../lib/useWriteControl", () => ({
  useWriteControl: () => ({ allowed: false, hidden: false, reason: viewerReason })
}));

vi.mock("../../lib/auth", () => ({
  useAuth: () => ({
    api: { token: "viewer-token" },
    token: "viewer-token",
    role: "viewer"
  })
}));

vi.mock("../../pages/table-detail/TableDetailContext", () => ({
  useTableDetailContext: () => ({
    tableId: "table-1",
    run: { isPending: false, isSuccess: false, isError: false, error: null, mutate: vi.fn(), variables: undefined },
    schedule: { isPending: false, isSuccess: false, isError: false, error: null, mutate: vi.fn(), variables: undefined }
  })
}));

vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return {
    ...actual,
    getPropertyRecommendations: vi.fn().mockResolvedValue({
      table_id: "table-1",
      generated_at: "2026-09-28T00:00:00Z",
      gc_blocked: false,
      items: [
        {
          property_key: "write.target-file-size-bytes",
          current_value: null,
          recommended_value: "268435456",
          reason: "Set a target file size",
          tier: "tier1",
          action: "apply",
          layout_recommendation_id: null,
          bloom_filter_column: null,
          needs_confirmation: false
        }
      ]
    }),
    listQualityConstraints: vi.fn().mockResolvedValue({
      table_id: "table-1",
      constraints: [],
      columns: []
    })
  };
});

const renderWithClient = (ui: ReactElement) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
};

describe("viewer write gates", () => {
  it("disables schedule, property apply, quality constraint add, and WAIR analyze with a reason", async () => {
    renderWithClient(
      <ActNowSection
        signals={{ signals: [] } as never}
        signalsLoading={false}
        signalsError={null}
        costView={{ by_operation: [{ operation: "optimize", savings_usd_month: 12, reclaimable_bytes: 100 }] } as never}
        stats={{ total_files: 1 }}
        statsRaw={{}}
        reclaimableBytes={100}
        savingsUsd={12}
        effectivePresetId="preset-1"
        onRun={vi.fn()}
        runPending={false}
        onOpenRun={vi.fn()}
        onPreview={vi.fn()}
        onSchedule={vi.fn()}
        schedulePending={false}
        tableLabel="raw.events"
      />
    );
    const schedule = screen.getByRole("button", { name: /Schedule/ });
    expect(schedule).toBeDisabled();
    expect(schedule).toHaveAttribute("title", viewerReason);
    expect(screen.getAllByText(viewerReason).length).toBeGreaterThan(0);

    renderWithClient(<PropertyRecommendations />);
    const apply = await screen.findByRole("button", { name: "Apply" });
    expect(apply).toBeDisabled();
    expect(apply).toHaveAttribute("title", viewerReason);

    renderWithClient(<QualityConstraints />);
    const add = screen.getByRole("button", { name: "Add rule" });
    expect(add).toBeDisabled();
    expect(add).toHaveAttribute("title", viewerReason);

    renderWithClient(<WairRecluster />);
    const analyze = screen.getByRole("button", { name: "Analyze" });
    expect(analyze).toBeDisabled();
    expect(analyze).toHaveAttribute("title", viewerReason);
    await waitFor(() => {
      expect(screen.getAllByText(viewerReason).length).toBeGreaterThan(0);
    });
  });
});
