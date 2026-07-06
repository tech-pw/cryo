import { describe, expect, it } from "vitest";
import type { AwsSourceStatus } from "./AwsStatusBadge";

const isConnected = (
  status: AwsSourceStatus
): status is Extract<AwsSourceStatus, { kind: "connected" }> => status.kind === "connected";

describe("AwsSourceStatus type guard", () => {
  it("identifies connected statuses", () => {
    expect(
      isConnected({ kind: "connected", enabled: true, lastTestOk: true, bucket: "b", region: "r" })
    ).toBe(true);
  });

  it("rejects non-connected statuses", () => {
    expect(isConnected({ kind: "loading" })).toBe(false);
    expect(isConnected({ kind: "missing" })).toBe(false);
    expect(isConnected({ kind: "error" })).toBe(false);
  });
});