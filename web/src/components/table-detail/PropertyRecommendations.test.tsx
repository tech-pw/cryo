import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PropertyRecommendations } from "./PropertyRecommendations";

vi.mock("../../lib/useWriteControl", () => ({
  useWriteControl: () => ({ allowed: true, hidden: false, reason: null })
}));

vi.mock("../../lib/auth", () => ({
  useAuth: () => ({ api: { token: "t" }, token: "t", role: "operator" })
}));

vi.mock("../../pages/table-detail/TableDetailContext", () => ({
  useTableDetailContext: () => ({
    tableId: "table-1",
    run: { isPending: false, isSuccess: false, isError: false, error: null, mutate: vi.fn(), variables: undefined }
  })
}));

const client = vi.hoisted(() => ({
  getPropertyRecommendations: vi.fn(),
  applyTableProperties: vi.fn(),
  getJob: vi.fn()
}));

vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return { ...actual, ...client };
});

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }));
vi.mock("sonner", () => ({ toast }));

const renderPanel = () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <PropertyRecommendations />
      </QueryClientProvider>
    </MemoryRouter>
  );
};

describe("PropertyRecommendations on a v3 table", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    client.getPropertyRecommendations.mockResolvedValue({
      table_id: "table-1",
      generated_at: "2026-11-16T00:00:00Z",
      gc_blocked: false,
      format_version: 3,
      property_route: { format_version: 3, route: "engine", engine: "spark_emr", destination_name: "spark" },
      items: [
        {
          property_key: "write.distribution-mode",
          current_value: null,
          recommended_value: "hash",
          reason: "Cluster writes",
          tier: "tier1",
          action: "apply",
          layout_recommendation_id: null,
          bloom_filter_column: null,
          needs_confirmation: false
        }
      ]
    });
    client.applyTableProperties.mockResolvedValue({
      table_id: "table-1",
      applied: { "write.distribution-mode": "hash" },
      audit_log_id: 7,
      message: "Applying 1 table property(ies) through spark_emr (spark).",
      route: "engine",
      engine: "spark_emr",
      status: "queued",
      run_id: "run-1",
      job_id: "job-1"
    });
  });

  it("shows Applying… and follows the job instead of claiming the change is done", async () => {
    client.getJob.mockResolvedValue({ id: "job-1", status: "running" });
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "Apply" }));

    const status = await screen.findByTestId("property-change-status");
    expect(within(status).getByText(/write.distribution-mode = hash/)).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /Applying…/ })).toBeDisabled();
    expect(toast.success).not.toHaveBeenCalled();
    expect(toast.info).toHaveBeenCalledWith("Applying write.distribution-mode through spark_emr…");
    await waitFor(() => expect(client.getJob).toHaveBeenCalledWith(expect.anything(), "job-1"));
  });

  it("reports a failed job with its error", async () => {
    client.getJob.mockResolvedValue({ id: "job-1", status: "failed", error: "AnalysisException: bad key" });
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "Apply" }));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "write.distribution-mode = hash failed: AnalysisException: bad key"
      )
    );
    await waitFor(() => expect(screen.queryByTestId("property-change-status")).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Apply" })).toBeEnabled();
  });

  it("keeps the synchronous result for a PyIceberg apply", async () => {
    client.applyTableProperties.mockResolvedValue({
      table_id: "table-1",
      applied: { "write.distribution-mode": "hash" },
      audit_log_id: 8,
      message: "Applied 1 table property(ies).",
      route: "pyiceberg",
      engine: "pyiceberg",
      status: "applied"
    });
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "Apply" }));

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("write.distribution-mode set to hash"));
    expect(client.getJob).not.toHaveBeenCalled();
    expect(screen.queryByTestId("property-change-status")).not.toBeInTheDocument();
  });
});
