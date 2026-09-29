import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SavingsByOperation } from "./SavingsByOperation";

describe("SavingsByOperation", () => {
  it("renders a ranked list and no pie", () => {
    const { container } = render(
      <SavingsByOperation
        items={[
          { operation: "expire_snapshots", savings_usd_month: 100, reclaimable_bytes: 0 },
          { operation: "optimize", savings_usd_month: 200, reclaimable_bytes: 50 }
        ]}
      />
    );

    const rows = screen.getAllByRole("listitem");
    expect(rows.map((row) => row.textContent)).toEqual(["Compaction$200/mo", "Snapshot expiry$100/mo"]);
    expect(container.querySelector("svg")).toBeNull();
    expect(screen.queryByText("0 GB")).not.toBeInTheDocument();
  });

  it("does not invent a zero-savings split when nothing was measured", () => {
    render(
      <SavingsByOperation
        items={[{ operation: "optimize", savings_usd_month: 0, reclaimable_bytes: 0 }]}
      />
    );

    expect(screen.getByText("No per-operation split")).toBeInTheDocument();
    expect(screen.queryByText("$0")).not.toBeInTheDocument();
    expect(screen.queryByText("0 GB")).not.toBeInTheDocument();
  });
});
