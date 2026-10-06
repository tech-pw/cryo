import { describe, expect, it } from "vitest";
import { formatSourceTestSuccessMessage } from "./Connections";
import { formatDestinationTestSummary, isRedactedPlaceholder } from "./connections/helpers";
import { REDACTED_PLACEHOLDER } from "./connections/constants";

describe("formatSourceTestSuccessMessage", () => {
  it("uses singular schema wording for one namespace", () => {
    expect(formatSourceTestSuccessMessage(1)).toBe("Connected — found 1 schema in catalog");
  });
  it("uses plural schemas wording for multiple namespaces", () => {
    expect(formatSourceTestSuccessMessage(199)).toBe("Connected — found 199 schemas in catalog");
  });
});

describe("isRedactedPlaceholder", () => {
  it("recognizes the exact backend sentinel string", () => {
    expect(isRedactedPlaceholder(REDACTED_PLACEHOLDER)).toBe(true);
  });

  it("rejects any other value, including near-matches", () => {
    expect(isRedactedPlaceholder("***redacted***")).toBe(false);
    expect(isRedactedPlaceholder("")).toBe(false);
    expect(isRedactedPlaceholder(undefined)).toBe(false);
  });
});

describe("formatDestinationTestSummary", () => {
  it("describes free maintenance capacity slots in plain English, not as storage", () => {
    const summary = formatDestinationTestSummary({
      ok: true,
      name: "prod-trino",
      capacity: 10,
      used: 3,
      available: 7
    });
    expect(summary).toContain("prod-trino");
    expect(summary).toContain("7 of 10 maintenance capacity slots free");
    expect(summary).toContain("3 in use");
    expect(summary.toLowerCase()).not.toContain("storage");
    expect(summary.toLowerCase()).not.toContain("byte");
  });

  it("falls back to a generic reachable message when no capacity numbers are present", () => {
    const summary = formatDestinationTestSummary({ ok: true, name: "spark-cluster" });
    expect(summary).toBe('"spark-cluster" is reachable and ready to run maintenance.');
  });

  it("reports failure without fabricating capacity numbers", () => {
    const summary = formatDestinationTestSummary({ ok: false, name: "broken-engine" });
    expect(summary).toBe('"broken-engine" isn\'t ready to run maintenance right now.');
  });
});
