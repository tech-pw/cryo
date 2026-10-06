import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AssetHeader } from "./AssetHeader";

vi.mock("../../lib/auth", () => ({
  useAuth: () => ({ subject: "tester", api: { token: "t" }, token: "t" })
}));

vi.mock("../../lib/useWriteControl", () => ({
  useWriteControl: () => ({ allowed: false, reason: "viewer" })
}));

describe("AssetHeader", () => {
  it("shows catalog › schema › table and Not scanned when there is no grade", () => {
    render(
      <AssetHeader
        navigate={() => undefined}
        row={
          {
            catalog_name: "prod",
            schema_name: "raw",
            table_name: "events",
            source_id: "src"
          } as never
        }
        stats={null}
        sourceName="lake"
        lastSnapshotCommittedAt={null}
        score={0}
        savingsDisplay="—"
        reclaimableBytes={0}
        partitionColLabel="—"
        hideSinglePartitionKpi
        scan={{ isPending: false } as never}
        queuedScanRunId={null}
        handleScanAction={() => undefined}
      />
    );

    const crumb = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(crumb).toHaveTextContent("prod");
    expect(crumb).toHaveTextContent("raw");
    expect(crumb).toHaveTextContent("events");
    expect(crumb).toHaveTextContent("›");
    expect(screen.getAllByText("Not scanned").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /Maintain/i })).not.toBeInTheDocument();
  });
});
