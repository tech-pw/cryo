import { describe, expect, it } from "vitest";

import { funnelCountLabel } from "./funnelLabel";

describe("funnelCountLabel", () => {
  it("does not render a missing stage as zero", () => {
    expect(funnelCountLabel(null)).toBe("Not recorded");
    expect(funnelCountLabel(0)).toBe("0");
  });
});
