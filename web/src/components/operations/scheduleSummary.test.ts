import { describe, expect, it } from "vitest";
import { describeCronSchedule, nextCronRun } from "./scheduleSummary";

describe("describeCronSchedule", () => {
  it("returns null for empty/blank input", () => {
    expect(describeCronSchedule(null)).toBeNull();
    expect(describeCronSchedule(undefined)).toBeNull();
    expect(describeCronSchedule("")).toBeNull();
    expect(describeCronSchedule("   ")).toBeNull();
  });

  it("describes a daily schedule in UTC", () => {
    expect(describeCronSchedule("0 2 * * *")).toBe("Daily at 02:00 UTC");
    expect(describeCronSchedule("30 9 * * *")).toBe("Daily at 09:30 UTC");
  });

  it("describes an hourly-interval schedule", () => {
    expect(describeCronSchedule("0 */6 * * *")).toBe("Every 6 hours (UTC)");
    expect(describeCronSchedule("0 */12 * * *")).toBe("Every 12 hours (UTC)");
  });

  it("describes a weekday-range schedule", () => {
    expect(describeCronSchedule("0 2 * * 1-5")).toBe("Mon–Fri at 02:00 UTC");
  });

  it("describes a single weekday schedule", () => {
    expect(describeCronSchedule("0 9 * * 0")).toBe("Weekly on Sun at 09:00 UTC");
  });

  it("falls back to the raw expression for unrecognized shapes", () => {
    expect(describeCronSchedule("*/15 * * * *")).toBe("*/15 * * * *");
    expect(describeCronSchedule("0 2 1 * *")).toBe("0 2 1 * *");
  });

  it("falls back to the raw string for malformed cron (wrong field count)", () => {
    expect(describeCronSchedule("0 2 * *")).toBe("0 2 * *");
  });
});

describe("nextCronRun", () => {
  it("returns null for empty or malformed cron", () => {
    expect(nextCronRun(null)).toBeNull();
    expect(nextCronRun("0 2 * *")).toBeNull();
  });

  it("finds the next daily run today when the time hasn't passed yet", () => {
    const from = new Date(Date.UTC(2026, 0, 15, 1, 0, 0));
    const next = nextCronRun("0 2 * * *", from);
    expect(next?.toISOString()).toBe(new Date(Date.UTC(2026, 0, 15, 2, 0, 0)).toISOString());
  });

  it("rolls over to the next day when today's time has already passed", () => {
    const from = new Date(Date.UTC(2026, 0, 15, 3, 0, 0));
    const next = nextCronRun("0 2 * * *", from);
    expect(next?.toISOString()).toBe(new Date(Date.UTC(2026, 0, 16, 2, 0, 0)).toISOString());
  });

  it("respects a weekday range (Mon-Fri), skipping the weekend", () => {
    // 2026-01-17 is a Saturday; next Mon-Fri 2am run should be Monday 2026-01-19.
    const from = new Date(Date.UTC(2026, 0, 17, 3, 0, 0));
    const next = nextCronRun("0 2 * * 1-5", from);
    expect(next?.toISOString()).toBe(new Date(Date.UTC(2026, 0, 19, 2, 0, 0)).toISOString());
  });

  it("respects an hourly-interval schedule", () => {
    const from = new Date(Date.UTC(2026, 0, 15, 1, 10, 0));
    const next = nextCronRun("0 */6 * * *", from);
    expect(next?.toISOString()).toBe(new Date(Date.UTC(2026, 0, 15, 6, 0, 0)).toISOString());
  });
});
