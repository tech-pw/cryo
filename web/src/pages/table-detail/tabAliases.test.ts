import { describe, expect, it } from "vitest";
import { translateTableDetailLocation } from "./tabAliases";

describe("table detail hash aliases", () => {
  it("sends property anchors to Config", () => {
    const health = translateTableDetailLocation(
      new URLSearchParams("tab=health-quality"),
      "#hq-properties"
    );
    expect(health.search.get("tab")).toBe("config");
    expect(health.hash).toBe("#config-properties");

    const quality = translateTableDetailLocation(new URLSearchParams("tab=quality"), "#hq-properties");
    expect(quality.search.get("tab")).toBe("config");
    expect(quality.hash).toBe("#config-properties");

    const files = translateTableDetailLocation(new URLSearchParams("tab=files"), "#files-properties");
    expect(files.search.get("tab")).toBe("config");
  });

  it("sends the optimization checklist to Overview", () => {
    const old = translateTableDetailLocation(new URLSearchParams("tab=quality"), "#quality-checklist");
    expect(old.search.get("tab")).toBeNull();
    expect(old.hash).toBe("#overview-checks");
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
