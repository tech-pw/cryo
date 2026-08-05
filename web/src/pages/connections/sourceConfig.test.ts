import { describe, expect, it } from "vitest";
import { REDACTED_PLACEHOLDER } from "./constants";
import { buildSourceConfig, decomposeSourceConfig } from "./sourceConfig";

describe("decomposeSourceConfig", () => {
  it("never surfaces the raw redaction placeholder into the editable secret field", () => {
    const decomposed = decomposeSourceConfig({
      "s3.access-key-id": "AKIAEXAMPLE",
      "s3.secret-access-key": REDACTED_PLACEHOLDER
    });
    expect(decomposed.s3_secret_access_key).toBeUndefined();
    expect(decomposed.s3_has_secret_access_key).toBe(true);
  });

  it("has no secret flag set when no secret has ever been saved", () => {
    const decomposed = decomposeSourceConfig({ "s3.access-key-id": "AKIAEXAMPLE" });
    expect(decomposed.s3_secret_access_key).toBeUndefined();
    expect(decomposed.s3_has_secret_access_key).toBe(false);
  });

  it("still surfaces a real (non-placeholder) value if one were ever present", () => {
    const decomposed = decomposeSourceConfig({ "s3.secret-access-key": "real-value-not-normally-sent" });
    expect(decomposed.s3_secret_access_key).toBe("real-value-not-normally-sent");
    expect(decomposed.s3_has_secret_access_key).toBe(true);
  });
});

describe("buildSourceConfig", () => {
  const base = {
    name: "demo",
    type: "rest",
    s3_auth_method: "access_key" as const,
    s3_access_key_id: "AKIAEXAMPLE",
    enabled: true
  };

  it("resubmits the redaction sentinel when the secret field is left blank on an edit that already has one", () => {
    const config = buildSourceConfig({ ...base, s3_secret_access_key: "", s3_has_secret_access_key: true });
    expect(config["s3.secret-access-key"]).toBe(REDACTED_PLACEHOLDER);
  });

  it("does not invent a secret key when none was ever saved and the field is left blank", () => {
    const config = buildSourceConfig({ ...base, s3_secret_access_key: "", s3_has_secret_access_key: false });
    expect(config["s3.secret-access-key"]).toBeUndefined();
  });

  it("sends the new value verbatim when the user types a replacement secret", () => {
    const config = buildSourceConfig({
      ...base,
      s3_secret_access_key: "brand-new-secret",
      s3_has_secret_access_key: true
    });
    expect(config["s3.secret-access-key"]).toBe("brand-new-secret");
  });
});
