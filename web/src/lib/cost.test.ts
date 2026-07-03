import { describe, expect, it } from "vitest";
import { deriveCostDashboard } from "../api/client";
import type { TableRead } from "../api/types";
import { buildWaterfallRows } from "../components/CostSavingsWaterfall";

describe("buildWaterfallRows", () => {
  it("builds current to projected savings without negative bars", () => {
    const rows = buildWaterfallRows(
      100,
      new Map([
        ["expire_snapshots", 30],
        ["remove_orphan_files", 10],
        ["optimize", 5]
      ])
    );

    expect(rows[0]).toMatchObject({ name: "Now", value: 100, kind: "total" });
    expect(rows.at(-1)).toMatchObject({ name: "After maintenance", value: 55, kind: "total" });
    expect(rows.every((row) => row.base >= 0 && row.value >= 0)).toBe(true);
    expect(rows.filter((row) => row.kind === "save")).toHaveLength(3);
  });

  it("drops zero-saving operations", () => {
    const rows = buildWaterfallRows(50, new Map([["optimize", 0], ["expire_snapshots", 10]]));
    expect(rows.some((row) => row.name === "Compaction")).toBe(false);
  });
});

describe("deriveCostDashboard", () => {
  const tables: TableRead[] = [
    {
      id: "1",
      source_id: "s1",
      schema_name: "analytics",
      table_name: "events",
      first_seen_at: "2026-01-01T00:00:00Z",
      is_active: true,
      stats: {
        estimated_monthly_s3_cost_usd: 80,
        projected_monthly_s3_cost_usd: 40,
        projected_savings_usd_month: 40,
        reclaimable_bytes: 1024 ** 3,
        total_size_gb: 10
      }
    },
    {
      id: "2",
      source_id: "s1",
      schema_name: "raw",
      table_name: "clicks",
      first_seen_at: "2026-01-01T00:00:00Z",
      is_active: true,
      stats: {
        estimated_monthly_s3_cost_usd: 20,
        total_size_gb: 2
      }
    }
  ];

  it("sums projected fields and sorts opportunities", () => {
    const dashboard = deriveCostDashboard(tables);
    expect(dashboard.totals.current_monthly_cost_usd).toBe(100);
    expect(dashboard.totals.projected_monthly_cost_usd).toBe(60);
    expect(dashboard.totals.projected_savings_usd_month).toBe(40);
    expect(dashboard.top_opportunities[0]?.table_name).toBe("events");
    expect(dashboard.cost_by_schema[0]?.schema_name).toBe("analytics");
  });

  it("returns zeroed per-operation savings when only table stats exist", () => {
    const dashboard = deriveCostDashboard(tables);
    expect(dashboard.savings_by_operation.every((item) => item.savings_usd_month === 0)).toBe(true);
  });
});
