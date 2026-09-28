import { describe, expect, it } from "vitest";

import { SCAN_ONLY_REASON, VIEWER_REASON, writeAccess } from "./permissions";

describe("writeAccess", () => {
  it("disables every operator and admin action for a viewer", () => {
    const actions = [
      "purgeSource",
      "patchSource",
      "cancelRun",
      "manifestRewrite",
      "approveLayout",
      "executeLayout",
      "dismissLayout",
      "triggerScan",
      "assignPreset",
      "editCompliance"
    ] as const;
    for (const action of actions) {
      const access = writeAccess("viewer", action);
      expect(access.allowed, action).toBe(false);
      if (action === "purgeSource") {
        expect(access.hidden).toBe(true);
      } else {
        expect(access.reason).toBe(VIEWER_REASON);
      }
    }
  });

  it("hides admin-only deletes from operators and blocks maintenance writes when scanning only", () => {
    expect(writeAccess("operator", "purgeSource").hidden).toBe(true);
    expect(writeAccess("operator", "cancelRun", false)).toMatchObject({
      allowed: false,
      reason: SCAN_ONLY_REASON
    });
    expect(writeAccess("admin", "purgeSource").allowed).toBe(true);
    expect(writeAccess("operator", "triggerScan", false).allowed).toBe(true);
  });
});
