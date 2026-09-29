import { describe, expect, it } from "vitest";

import { VIEWER_REASON, writeAccess } from "./permissions";

describe("viewer write gates", () => {
  it("disables dark-table dismiss and anomaly refresh for a viewer", () => {
    expect(writeAccess("viewer", "dismissDarkTable")).toMatchObject({
      allowed: false,
      reason: VIEWER_REASON
    });
    expect(writeAccess("viewer", "refreshAnomalies").allowed).toBe(false);
    expect(writeAccess("operator", "dismissDarkTable").allowed).toBe(true);
    expect(writeAccess("operator", "refreshAnomalies").allowed).toBe(true);
  });
});
