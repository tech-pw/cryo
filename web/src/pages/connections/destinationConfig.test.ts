import { describe, expect, it } from "vitest";
import { REDACTED_PLACEHOLDER } from "./constants";
import { buildDestinationConfig, decomposeDestinationConfig, type DestinationFormValues } from "./destinationConfig";

describe("decomposeDestinationConfig (trino)", () => {
  it("never surfaces the raw redaction placeholder into the editable password field", () => {
    const decomposed = decomposeDestinationConfig("trino", {
      auth: { type: "basic", username: "cryo", password: REDACTED_PLACEHOLDER }
    });
    expect(decomposed.auth_password).toBe("");
    expect(decomposed.auth_has_password).toBe(true);
    expect(decomposed.use_basic_auth).toBe(true);
    expect(decomposed.auth_username).toBe("cryo");
  });

  it("has no password flag set when basic auth has never been configured", () => {
    const decomposed = decomposeDestinationConfig("trino", { coordinators: ["localhost"] });
    expect(decomposed.auth_password).toBe("");
    expect(decomposed.auth_has_password).toBe(false);
    expect(decomposed.use_basic_auth).toBe(false);
  });
});

describe("buildDestinationConfig (trino auth)", () => {
  const base: DestinationFormValues = {
    name: "demo",
    engine: "trino",
    engine_virtual: "trino",
    enabled: true,
    coordinators: [{ host: "localhost", port: 8080, http_scheme: "http" }],
    user: "cryo",
    catalog: "iceberg",
    use_basic_auth: true,
    auth_username: "cryo"
  };

  it("resubmits the redaction sentinel when the password field is left blank on an edit that already has one", () => {
    const config = buildDestinationConfig({ ...base, auth_password: "", auth_has_password: true });
    expect((config.auth as Record<string, unknown>).password).toBe(REDACTED_PLACEHOLDER);
  });

  it("does not invent a password when none was ever saved and the field is left blank", () => {
    const config = buildDestinationConfig({ ...base, auth_password: "", auth_has_password: false });
    expect((config.auth as Record<string, unknown>).password).toBe("");
  });

  it("sends the new value verbatim when the user types a replacement password", () => {
    const config = buildDestinationConfig({
      ...base,
      auth_password: "brand-new-password",
      auth_has_password: true
    });
    expect((config.auth as Record<string, unknown>).password).toBe("brand-new-password");
  });
});
