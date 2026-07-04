import { describe, expect, it } from "vitest";
import { normalizeCronExpression, validateCronExpression } from "./cron";

describe("cron validation", () => {
  it("accepts supported five-field cron expressions", () => {
    expect(validateCronExpression("0 2 * * *")).toBeNull();
    expect(validateCronExpression("0 */6 * * *")).toBeNull();
    expect(validateCronExpression("0 2 * * 1-5")).toBeNull();
  });

  it("normalizes extra spaces", () => {
    expect(normalizeCronExpression("  0   2   *   *   *  ")).toBe("0 2 * * *");
  });

  it("allows blank values only for optional schedules", () => {
    expect(validateCronExpression("", { allowEmpty: true })).toBeNull();
    expect(validateCronExpression("")).toBe("Cron expression is required");
  });

  it("rejects out-of-range values and invalid steps", () => {
    expect(validateCronExpression("0 25 * * *")).toBe("Cron value is outside the supported bounds");
    expect(validateCronExpression("0 */0 * * *")).toBe("Cron step values must be positive");
    expect(validateCronExpression("0 */ * * *")).toBe("Cron step values must be integers");
  });

  it("rejects malicious or unsafe input", () => {
    expect(validateCronExpression("0 2 * * *; DROP TABLE cryo.tables")).toBe(
      "Cron expression contains unsupported characters"
    );
    expect(validateCronExpression("0 2 * * *\n* * * * *")).toBe(
      "Cron expression contains unsupported characters"
    );
    expect(validateCronExpression("0 2 * * *\x00")).toBe(
      "Cron expression contains unsupported characters"
    );
    expect(validateCronExpression("0 2 * * *" + " ".repeat(101))).toBe("Cron expression is too long");
  });
});
