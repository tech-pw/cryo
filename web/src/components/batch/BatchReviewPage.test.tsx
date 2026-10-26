import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { BatchReviewPage } from "./BatchReviewPage";

const mocks = vi.hoisted(() => ({
  listDestinations: vi.fn(),
  listPresets: vi.fn(),
  runMaintenance: vi.fn(),
  toastSuccess: vi.fn()
}));

vi.mock("../../api/client", () => ({
  listDestinations: mocks.listDestinations,
  listPresets: mocks.listPresets,
  runMaintenance: mocks.runMaintenance
}));

vi.mock("sonner", () => ({ toast: { success: mocks.toastSuccess, error: vi.fn() } }));

vi.mock("../../lib/auth", () => ({
  useAuth: () => ({ api: { token: "t" }, token: "t", subject: "pw" })
}));

vi.mock("../../lib/maintenanceBatch", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../lib/maintenanceBatch")>();
  return {
    ...original,
    loadDraft: (identity: Parameters<typeof original.createEmptyDraft>[0]) => ({
      ...original.createEmptyDraft(identity),
      destinationId: "d1",
      presetId: "p1",
      tables: [{ id: "t1", label: "lake › s › events" }]
    })
  };
});

const renderPage = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <BatchReviewPage />
      </MemoryRouter>
    </QueryClientProvider>
  );

describe("BatchReviewPage force", () => {
  beforeEach(() => {
    mocks.listDestinations.mockReset().mockResolvedValue([{ id: "d1", name: "trino" }]);
    mocks.listPresets.mockReset().mockResolvedValue([{ id: "p1", name: "nightly", destination_id: "d1", mode: "standard" }]);
    mocks.runMaintenance.mockReset().mockResolvedValue({ run_id: "run-1", job_ids: ["j1"], status: "queued" });
    mocks.toastSuccess.mockReset();
  });

  it("omits force by default and sends it once the operator opts in", async () => {
    renderPage();
    const runButton = await screen.findByRole("button", { name: /^Run/ });

    fireEvent.click(runButton);
    await waitFor(() => expect(mocks.runMaintenance).toHaveBeenCalledTimes(1));
    expect(mocks.runMaintenance.mock.calls[0][1]).not.toHaveProperty("force");

    fireEvent.click(screen.getByRole("checkbox", { name: /Run even if already queued/ }));
    fireEvent.click(runButton);
    await waitFor(() => expect(mocks.runMaintenance).toHaveBeenCalledTimes(2));
    expect(mocks.runMaintenance.mock.calls[1][1]).toMatchObject({ force: true });
  });

  it("tells the operator which steps were skipped as already queued", async () => {
    mocks.runMaintenance.mockResolvedValue({
      run_id: "run-2",
      job_ids: ["j1"],
      status: "queued",
      skipped: [{ table_id: "t9", operations: ["optimize", "expire_snapshots"], covering_job_ids: ["x"], fully_skipped: false }]
    });
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: /^Run/ }));

    await waitFor(() =>
      expect(mocks.toastSuccess).toHaveBeenCalledWith(
        "Run queued: run-2",
        expect.objectContaining({ description: expect.stringContaining("Skipped 2 steps on 1 table") })
      )
    );
  });
});
