import { describe, expect, it } from "vitest";
import type { RunRead } from "../api/types";
import { realizedSavingsLabel } from "./operations";

const run = (overrides: Partial<RunRead>): RunRead => ({
  id: "run-1",
  kind: "scheduled",
  status: "succeeded",
  created_at: "2026-09-27T00:00:00Z",
  ...overrides
});

describe("realizedSavingsLabel", () => {
  it("does not treat a missing byte count as zero", () => {
    expect(
      realizedSavingsLabel(
        run({ realized_actions_succeeded: 4, realized_actions_measured: 4, realized_bytes_freed: null })
      )
    ).toBe("Bytes unknown");
  });

  it("says unknown when no byte-capable action reported a size", () => {
    expect(
      realizedSavingsLabel(run({ realized_actions_succeeded: 3, realized_actions_measured: 0 }))
    ).toBe("Bytes unknown");
  });

  it("shows a measured size and the coverage gap", () => {
    expect(
      realizedSavingsLabel(
        run({
          realized_actions_succeeded: 2,
          realized_actions_measured: 1,
          realized_bytes_freed: 1024 ** 3
        })
      )
    ).toBe("Freed 1 GB (1 of 2 actions measured)");
  });
});
