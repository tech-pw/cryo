import { describe, expect, it } from "vitest";
import { deriveCostDashboard } from "./client";
import type { TableRead } from "./types";

const table = (overrides: Partial<TableRead> & Pick<TableRead, "id" | "schema_name" | "table_name">): TableRead => ({
  source_id: "s1",
  first_seen_at: "2026-01-01T00:00:00Z",
  is_active: true,
  ...overrides
});

describe("deriveCostDashboard unknown costs", () => {
  it("does not record a null table cost as $0", () => {
    const dashboard = deriveCostDashboard([
      table({
        id: "missing",
        schema_name: "raw",
        table_name: "unpriced",
        stats: {
          estimated_monthly_s3_cost_usd: null,
          total_size_gb: 4,
          reclaimable_bytes: null
        } as unknown as TableRead["stats"]
      }),
      table({
        id: "measured",
        schema_name: "analytics",
        table_name: "events",
        stats: { estimated_monthly_s3_cost_usd: 12, total_size_gb: 1, reclaimable_bytes: 100 }
      })
    ]);

    expect(dashboard.totals.current_monthly_cost_usd).toBe(12);
    expect(dashboard.cost_by_schema.map((row) => row.schema_name)).toEqual(["analytics"]);
    expect(dashboard.cost_by_schema.some((row) => row.current_usd === 0)).toBe(false);
    expect(dashboard.totals.reclaimable_bytes).toBe(100);
  });
});
