import { describe, expect, it } from "vitest";

import { auditBytesLabel, auditFilesLabel } from "./auditFeed";

describe("audit feed labels", () => {
  it("never renders unknown bytes as zero", () => {
    expect(auditBytesLabel(null)).toBe("Unknown");
    expect(auditBytesLabel(undefined)).toBe("Unknown");
    expect(auditBytesLabel(0)).toBe("0 GB");
    expect(auditFilesLabel(null, 1)).toBe("Unknown");
    expect(auditFilesLabel(12, 3)).toBe("12 → 3");
  });
});
