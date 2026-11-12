import { describe, expect, it } from "vitest";
import type { BlockingReasonRead } from "../api/maintenanceStatus";
import { blockingReasonText } from "./maintenanceReasons";

const cooldown = (until: string, hours: number): BlockingReasonRead => ({
  code: "cooldown",
  summary: `Cooldown until ${until} (last succeeded or partial finish x, cooldown_hours=${hours})`,
  predicate: "load_candidates",
  blocks_nightly_selection: true,
  until,
  values: { until, cooldown_hours: hours, finished_at: "x" }
});

describe("blockingReasonText", () => {
  it("renders cooldown as a short local-time sentence without internals", () => {
    const now = new Date();
    const until = new Date(now.getTime() + 60_000);
    until.setSeconds(0, 0);
    const text = blockingReasonText(cooldown(until.toISOString(), 20), now);
    expect(text).toMatch(/^In cooldown until \d{2}:\d{2} \(20 h after the last maintenance\)$/);
    expect(text).not.toContain("cooldown_hours");
    expect(text).not.toContain("T");
  });

  it("includes the date when the cooldown ends on another day", () => {
    const now = new Date(2026, 10, 12, 12, 0);
    const text = blockingReasonText(cooldown(new Date(2026, 10, 14, 9, 30).toISOString(), 36), now);
    expect(text).toContain("Nov");
    expect(text).toContain("36 h after the last maintenance");
  });

  it("falls back to the backend summary with readable instants for other reasons", () => {
    const text = blockingReasonText({ ...cooldown("2026-11-12T17:17:42+00:00", 1), code: "backoff", summary: "Back-off" });
    expect(text).toBe("Back-off");
  });
});
