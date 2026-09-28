import { describe, expect, it } from "vitest";
import {
  UNKNOWN,
  batchCounts,
  budgetLine,
  failureRows,
  filesTransition,
  isBatchPipeline,
  laneRows,
  percentChange,
  rewrittenBytes,
  sizeTransition
} from "./batchSummary";
import { formatBytesGb, formatGb, formatNumber } from "./format";

describe("batchSummary", () => {
  it("does not treat a batch run kind as a new kind signal", () => {
    expect(isBatchPipeline({ pipeline: "batch" })).toBe(true);
    expect(isBatchPipeline({ pipeline: "scheduled" })).toBe(false);
    expect(isBatchPipeline(null)).toBe(false);
    expect(isBatchPipeline(undefined)).toBe(false);
  });

  it("renders missing keys as Unknown and keeps a present zero", () => {
    expect(batchCounts({ pipeline: "batch" })).toEqual({
      selected: UNKNOWN,
      succeeded: UNKNOWN,
      failed: UNKNOWN,
      skipped: UNKNOWN,
      overBudget: UNKNOWN
    });
    expect(batchCounts({ pipeline: "batch", selected: null, failed: 0 }).selected).toBe(UNKNOWN);
    expect(batchCounts({ pipeline: "batch", selected: 0, failed: 0 }).selected).toBe("0");
    expect(batchCounts({ pipeline: "batch", selected: 0, failed: 0 }).failed).toBe("0");
    expect(batchCounts({ pipeline: "batch", over_budget: 4 }).overBudget).toBe("4");
  });

  it("formats file and size transitions without inventing a percent from a missing side", () => {
    expect(percentChange(null, 10)).toBe(UNKNOWN);
    expect(percentChange(0, 10)).toBe(UNKNOWN);
    expect(percentChange(1000, 400)).toBe("-60%");
    expect(percentChange(8, 10)).toBe("+25%");
    expect(filesTransition({ pipeline: "batch" })).toBe(`${UNKNOWN} → ${UNKNOWN} (${UNKNOWN})`);
    expect(filesTransition({ pipeline: "batch", files_before: 1000, files_after: null })).toBe(
      `${formatNumber(1000)} → ${UNKNOWN} (${UNKNOWN})`
    );
    expect(filesTransition({ pipeline: "batch", files_before: 1000, files_after: 400 })).toBe(
      `${formatNumber(1000)} → ${formatNumber(400)} (-60%)`
    );
    expect(sizeTransition({ pipeline: "batch", size_before_gb: 10, size_after_gb: 8 })).toBe(
      `${formatGb(10)} → ${formatGb(8)} (-20%)`
    );
    expect(sizeTransition({ pipeline: "batch", size_before_gb: 0 })).toBe(`0 GB → ${UNKNOWN} (${UNKNOWN})`);
  });

  it("formats rewritten bytes only when the key is a number", () => {
    expect(rewrittenBytes({})).toBe(UNKNOWN);
    expect(rewrittenBytes({ bytes_rewritten: null })).toBe(UNKNOWN);
    expect(rewrittenBytes({ bytes_rewritten: 0 })).toBe(formatBytesGb(0));
    expect(rewrittenBytes({ bytes_rewritten: 2 * 1024 ** 3 })).toBe(formatBytesGb(2 * 1024 ** 3));
  });

  it("maps lanes from cpu_milli and mem_mib and leaves missing lane fields unknown", () => {
    expect(laneRows({})).toBeNull();
    expect(laneRows({ lanes: null })).toBeNull();
    expect(
      laneRows({
        lanes: [{ lane: 0, cpu_milli: 2000, mem_mib: 8192, tables: 4 }, { cpu_milli: 1500, mem_mib: 512 }]
      })
    ).toEqual([
      { id: "lane-0-0", lane: "0", vcpu: "2", memory: "8 GiB", tables: "4" },
      { id: "lane-1-1", lane: UNKNOWN, vcpu: "1.5", memory: "512 MiB", tables: UNKNOWN }
    ]);
  });

  it("reads failures without turning a missing list into zero rows", () => {
    expect(failureRows({})).toBeNull();
    expect(failureRows({ failures: [] })).toEqual([]);
    expect(failureRows({ failures: [{ table: "db.events", error: "boom" }, { table: "" }] })).toEqual([
      { id: "failure-0", table: "db.events", error: "boom" },
      { id: "failure-1", table: UNKNOWN, error: UNKNOWN }
    ]);
  });

  it("formats the compute budget line from autocomp and does not zero-fill it", () => {
    expect(budgetLine({})).toBe(`${UNKNOWN} of ${UNKNOWN} GB·h`);
    expect(budgetLine({ autocomp: null })).toBe(`${UNKNOWN} of ${UNKNOWN} GB·h`);
    expect(budgetLine({ autocomp: { chosen_gbhr: 12.5, budget_gbhr: 40 } })).toBe("12.5 of 40 GB·h");
    expect(budgetLine({ autocomp: { chosen_gbhr: 0, budget_gbhr: 40 } })).toBe("0 of 40 GB·h");
    expect(budgetLine({ autocomp: { budget_gbhr: 40 } })).toBe(`${UNKNOWN} of 40 GB·h`);
  });
});
