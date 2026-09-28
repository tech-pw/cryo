import { describe, expect, it } from "vitest";
import { translateTableDetailLocation } from "./tabAliases";

describe("table detail hash aliases", () => {
  it("sends property anchors to Schema & files", () => {
    const health = translateTableDetailLocation(
      new URLSearchParams("tab=health-quality"),
      "#hq-properties"
    );
    expect(health.search.get("tab")).toBe("files");
    expect(health.hash).toBe("#files-properties");

    const quality = translateTableDetailLocation(new URLSearchParams("tab=quality"), "#hq-properties");
    expect(quality.search.get("tab")).toBe("files");
    expect(quality.hash).toBe("#files-properties");
  });

  it("sends other hq anchors to their new home", () => {
    const compaction = translateTableDetailLocation(new URLSearchParams("tab=health-quality"), "#hq-compaction");
    expect(compaction.search.get("tab")).toBe("maintenance");
    expect(compaction.hash).toBe("#maintenance-compaction");
  });

  it("drops an unknown hq anchor instead of leaving a broken hash", () => {
    const unknown = translateTableDetailLocation(new URLSearchParams("tab=quality"), "#hq-missing");
    expect(unknown.hash).toBe("");
    expect(unknown.search.get("tab")).toBe("quality");
  });
});
