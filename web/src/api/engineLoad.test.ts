import { describe, expect, it } from "vitest";

import { drainHint, type EngineLoad } from "./engineLoad";

const load = (over: Partial<EngineLoad>, slotsUsed = 2): EngineLoad => ({
  engines: [
    { destination_id: "d", name: "spark", engine: "spark", slots_used: slotsUsed, slots_limit: 8, admission: "open", live_probe: "ok" }
  ],
  workers_online: 4,
  workers_busy: 4,
  admitted_waiting: 0,
  oldest_waiting_seconds: null,
  ...over
});

describe("drainHint", () => {
  it("is silent when nothing is waiting", () => {
    expect(drainHint(load({}))).toBeNull();
    expect(drainHint(undefined)).toBeNull();
  });

  it("blames workers when an engine still has open slots", () => {
    expect(drainHint(load({ admitted_waiting: 140, oldest_waiting_seconds: 540 }))).toBe(
      "Workers are the bottleneck: 140 admitted jobs waiting, oldest 9 min. Add workers or turn on async Spark dispatch."
    );
  });

  it("blames engine slots when every engine is full", () => {
    expect(drainHint(load({ admitted_waiting: 1 }, 8))).toBe("Engine slots are the bottleneck: 1 admitted job waiting.");
  });
});
