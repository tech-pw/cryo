import { describe, expect, it } from "vitest";
import { formatSourceTestSuccessMessage } from "./Connections";

describe("formatSourceTestSuccessMessage", () => {
  it("uses singular schema wording for one namespace", () => {
    expect(formatSourceTestSuccessMessage(1)).toBe("Connected — found 1 schema in catalog");
  });

  it("uses plural schemas wording for multiple namespaces", () => {
    expect(formatSourceTestSuccessMessage(199)).toBe("Connected — found 199 schemas in catalog");
  });
});
