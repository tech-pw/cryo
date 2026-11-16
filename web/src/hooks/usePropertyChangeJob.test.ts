import { describe, expect, it } from "vitest";

import { isTerminalJobStatus, propertyChangeOutcome } from "./usePropertyChangeJob";

describe("property change job status", () => {
  it("keeps polling until the job reaches a terminal status", () => {
    for (const status of ["queued", "admitted", "running", "deferred", null, undefined]) {
      expect(isTerminalJobStatus(status)).toBe(false);
    }
    for (const status of ["succeeded", "failed", "partial", "skipped", "SUCCEEDED"]) {
      expect(isTerminalJobStatus(status)).toBe(true);
    }
  });

  it("reports only a succeeded job as applied", () => {
    expect(propertyChangeOutcome("succeeded")).toBe("succeeded");
    expect(propertyChangeOutcome("partial")).toBe("failed");
    expect(propertyChangeOutcome("skipped")).toBe("failed");
    expect(propertyChangeOutcome("failed")).toBe("failed");
  });
});
