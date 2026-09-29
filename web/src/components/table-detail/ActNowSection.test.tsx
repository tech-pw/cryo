import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ActNowSection } from "./ActNowSection";

const detail = vi.hoisted(() => ({
  run: {
    isPending: false,
    isSuccess: false,
    isError: false,
    error: null as Error | null,
    variables: undefined as unknown,
    mutate: vi.fn()
  },
  schedule: {
    isPending: false,
    isSuccess: false,
    isError: false,
    error: null as Error | null,
    variables: undefined as string | undefined,
    mutate: vi.fn()
  }
}));

vi.mock("../../lib/useWriteControl", () => ({
  useWriteControl: () => ({ allowed: true, hidden: false, reason: null })
}));

vi.mock("../../pages/table-detail/TableDetailContext", () => ({
  useTableDetailContext: () => detail
}));

const renderSection = (overrides: Partial<Parameters<typeof ActNowSection>[0]> = {}) =>
  render(
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
      {...overrides}
    />
  );

describe("ActNowSection savings", () => {
  it("reads null savings and reclaim as Not measured and does not score savings", () => {
    detail.run.isPending = false;
    detail.run.isSuccess = false;
    detail.run.isError = false;
    detail.run.error = null;
    renderSection({
      signals: {
        signals: [
          {
            signal_name: "storage_cost_anomaly",
            triggered: true,
            severity: "warning"
          }
        ]
      } as never,
      costView: { by_operation: [] } as never,
      reclaimableBytes: null,
      savingsUsd: null
    });
    const card = screen.getByText("Storage Cost & Resource Overhead Anomaly").closest(".td-act-now-card");
    expect(card).toHaveTextContent("Not measured");
    expect(card).not.toHaveTextContent("$");
    expect(card).not.toHaveTextContent("GB");
    expect(screen.queryByText(/potential savings/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Save \$/)).not.toBeInTheDocument();
  });
});

describe("ActNowSection mutation state", () => {
  it("does not show a queued banner before the run succeeds", () => {
    detail.run.isPending = false;
    detail.run.isSuccess = false;
    detail.run.isError = false;
    detail.run.error = null;
    renderSection();
    expect(screen.queryByText("Maintenance run queued for this table")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Act now" })).toBeInTheDocument();
  });

  it("shows the run error with retry and not the queued banner", () => {
    detail.run.isError = true;
    detail.run.error = new Error("engine rejected the run");
    const onRun = vi.fn();
    renderSection({ onRun });
    expect(screen.getByText(/engine rejected the run/)).toBeInTheDocument();
    expect(screen.queryByText("Maintenance run queued for this table")).not.toBeInTheDocument();
    screen.getByRole("button", { name: "Retry" }).click();
    expect(onRun).toHaveBeenCalled();
    detail.run.isError = false;
    detail.run.error = null;
  });
});
