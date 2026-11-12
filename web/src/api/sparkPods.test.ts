import { describe, expect, it } from "vitest";

import { podStateLabel, type SparkPod } from "./sparkPods";

const pod = (over: Partial<SparkPod>): SparkPod => ({
  role: "driver",
  name: "p",
  phase: "Running",
  waiting_reason: null,
  waiting_message: null,
  waiting_seconds: null,
  node: null,
  stuck: false,
  ...over
});

describe("podStateLabel", () => {
  it("shows the phase for a running pod", () => {
    expect(podStateLabel(pod({}))).toBe("Running");
  });

  it("shows the waiting reason and how long", () => {
    expect(podStateLabel(pod({ phase: "Pending", waiting_reason: "ImagePullBackOff", waiting_seconds: 725 }))).toBe(
      "ImagePullBackOff · 12m"
    );
  });
});
