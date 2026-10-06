import { describe, expect, it } from "vitest";
import { capabilitySupportLabel, capabilitySupportVariant } from "./CapabilitiesTab";

describe("capability presentation", () => {
  it("uses explicit operator language for non-executing states", () => {
    expect(capabilitySupportLabel("fallback_required")).toBe("Fallback");
    expect(capabilitySupportLabel("configuration_only")).toBe("Config only");
    expect(capabilitySupportLabel("unsupported")).toBe("Blocked");
  });

  it("renders blocked operations with danger emphasis", () => {
    expect(capabilitySupportVariant("unsupported")).toBe("danger");
    expect(capabilitySupportVariant("supported")).toBe("success");
  });
});
