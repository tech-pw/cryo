import { describe, expect, it } from "vitest";

import { parkedDetail, type ParkedJob } from "./asyncSpark";

const now = Date.UTC(2026, 10, 12, 12, 0, 0);
const job = (over: Partial<ParkedJob>): ParkedJob => ({
  job_id: "j",
  step_index: 0,
  spark_ref: "cryo-spark-j",
  parked_at: now / 1000 - 600,
  last_checked_at: now / 1000 - 30,
  next_check_at: now / 1000,
  overdue: false,
  ...over
});

describe("parkedDetail", () => {
  it("says when the job was submitted and last checked", () => {
    expect(parkedDetail(job({}), now)).toBe(
      "Step 1 is running on Spark without holding a worker. Submitted 10m ago. Last checked 1m ago."
    );
  });

  it("flags an overdue job and one never checked", () => {
    expect(parkedDetail(job({ last_checked_at: null, overdue: true }), now)).toContain(
      "Not checked yet. The governor has not checked it for several resolver ticks."
    );
  });
});
