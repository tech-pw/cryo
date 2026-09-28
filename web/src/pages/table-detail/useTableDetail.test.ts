import { describe, expect, it } from "vitest";
import { firstFinite, tableSavingsDisplay } from "./useTableDetail";

describe("table detail unknown cost", () => {
  it("does not turn a missing cost into zero", () => {
    expect(firstFinite(null, undefined)).toBeNull();
    expect(firstFinite(undefined, 0)).toBe(0);
    expect(
      tableSavingsDisplay({
        hasStats: false,
        hasCostView: false,
        projectedSavingsUsd: null,
        showLatencyBenefit: false
      })
    ).toBe("Not scanned");
    expect(
      tableSavingsDisplay({
        hasStats: true,
        hasCostView: false,
        projectedSavingsUsd: null,
        showLatencyBenefit: false
      })
    ).toBe("Cost data unavailable");
    expect(
      tableSavingsDisplay({
        hasStats: true,
        hasCostView: true,
        projectedSavingsUsd: 12,
        showLatencyBenefit: false
      })
    ).not.toBe("$0");
  });
});
