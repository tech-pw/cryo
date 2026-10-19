import { matchPath } from "react-router-dom";
import { describe, expect, it } from "vitest";

import {
  DESTINATIONS,
  RUNS_NESTED_PATHS,
  TABLE_DETAIL_TAB_ALIASES,
  commandPaletteEntries,
  firstRunsNestedMatch,
  resolveLegacyRedirect,
  translateTableDetailTab,
  type AppLocation
} from "./routes";

const parse = (input: string): AppLocation => {
  const hashAt = input.indexOf("#");
  const hash = hashAt >= 0 ? input.slice(hashAt) : "";
  const before = hashAt >= 0 ? input.slice(0, hashAt) : input;
  const queryAt = before.indexOf("?");
  return {
    pathname: queryAt >= 0 ? before.slice(0, queryAt) : before,
    search: queryAt >= 0 ? before.slice(queryAt) : "",
    hash
  };
};

const paramsOf = (search: string): Record<string, string> =>
  Object.fromEntries(new URLSearchParams(search.startsWith("?") ? search.slice(1) : search));

const expectRedirect = (input: string, output: string | null) => {
  const actual = resolveLegacyRedirect(parse(input));
  if (output === null) {
    expect(actual, input).toBeNull();
    return;
  }
  const expected = parse(output);
  expect(actual?.pathname, input).toBe(expected.pathname);
  expect(actual?.hash, input).toBe(expected.hash);
  expect(paramsOf(actual?.search ?? ""), input).toEqual(paramsOf(expected.search));
  expect(resolveLegacyRedirect(actual ?? expected), input).toBeNull();
};

describe("destination registry", () => {
  it("has the seven questions word for word", () => {
    expect(DESTINATIONS.map((destination) => [destination.label, destination.path, destination.subtitle])).toEqual([
      ["Home", "/", "What happened last night, and is the lake worse?"],
      ["Tables", "/tables", "Which tables, and what is wrong with this one?"],
      ["Runs", "/runs", "What ran, what failed, and what is held?"],
      ["Decisions", "/decisions", "What is waiting on a person, and why was a table skipped?"],
      ["Cost", "/cost", "What do we pay, what did maintenance change, and who is reading?"],
      ["Insights", "/insights", "Which tables are the same physical data, and which queries are slow?"],
      ["Settings", "/settings", "What is connected, and who may call the model?"]
    ]);
  });

  it("indexes old command names on the new paths", () => {
    const entries = commandPaletteEntries("admin");
    expect(entries.find((entry) => entry.label === "Dark tables")?.href).toBe("/tables?view=unused");
    expect(entries.find((entry) => entry.label === "Control Room")?.href).toBe("/runs?tab=holds");
    expect(entries.find((entry) => entry.label === "Scan cost")?.href).toBe("/cost?tab=reads");
    expect(entries.find((entry) => entry.label === "SLA")?.href).toBe("/tables?view=freshness");
    expect(entries.find((entry) => entry.label === "Smart maintenance")?.href).toBe(
      "/decisions?tab=recommendations"
    );
  });
});

describe("legacy redirects", () => {
  const cases: Array<[string, string | null]> = [
    ["/health", "/"],
    ["/health?scope=prod&period=7d", "/?scope=prod&period=7d"],
    ["/health?scope=prod#top", "/?scope=prod#top"],
    ["/", null],
    ["/?scope=prod", null],
    ["/sla", "/tables?view=freshness"],
    ["/sla?breached=1&q=foo&bucket=3_7d&page=2", "/tables?view=freshness&breached=1&q=foo&bucket=3_7d&page=2"],
    ["/catalogs", "/tables"],
    ["/catalogs?grades=D,F&sort=-small_files_count", "/tables?grades=D,F&sort=-small_files_count"],
    ["/tables", null],
    ["/tables?q=orders", null],
    ["/search?q=orders", "/tables?q=orders&notice=search"],
    ["/search?q=orders&group=columns&offset=50", "/tables?q=orders&group=columns&offset=50&notice=search"],
    ["/dark-tables", "/tables?view=unused"],
    ["/dark-tables?window=30", "/tables?view=unused&window=30"],
    ["/tables/table-1", null],
    ["/tables/table-1?tab=structure#col", null],
    ["/tables/table-1?tab=health-quality", null],
    ["/tables/table-1?tab=activity", null],
    ["/tables/table-1?tab=lineage", null],
    ["/operations", "/runs?tab=workflows"],
    ["/operations?tab=workflows&foo=1", "/runs?tab=workflows&foo=1"],
    ["/presets", "/runs?tab=workflows"],
    ["/operations?tab=runs", "/runs?tab=history"],
    ["/operations?tab=runs&runFilter=failed&runsPage=2", "/runs?tab=history&runFilter=failed&runsPage=2"],
    ["/runs", "/runs?tab=history"],
    ["/operations?tab=actions", "/runs?tab=actions"],
    ["/operations?tab=actions&operation=optimize&engine=trino", "/runs?tab=actions&operation=optimize&engine=trino"],
    ["/operations/runs/run-1", "/runs/run-1"],
    ["/operations/runs/run-1?job=j1#logs", "/runs/run-1?job=j1#logs"],
    ["/runs/run-1", null],
    ["/scans/run-1", "/runs/run-1"],
    ["/scans/run-1?job=j1#logs", "/runs/run-1?job=j1#logs"],
    ["/jobs/job-1", "/runs/jobs/job-1"],
    ["/jobs/job-1?x=1#top", "/runs/jobs/job-1?x=1#top"],
    ["/runs/jobs/job-1", null],
    ["/operations/presets/preset-1", "/runs/workflows/preset-1"],
    ["/operations/presets/preset-1?tab=config", "/runs/workflows/preset-1?tab=config"],
    ["/operations/scanner", "/runs/scanner"],
    ["/operations/scanner?tab=config", "/runs/scanner?tab=config"],
    ["/scans", "/runs/scanner"],
    ["/runs/scanner", null],
    ["/control-room", "/runs?tab=holds"],
    ["/control-room?scope=all", "/runs?tab=holds&scope=all"],
    ["/smart-maintenance", "/decisions?tab=recommendations"],
    ["/smart-maintenance?inspect=table-1", "/decisions?tab=recommendations&inspect=table-1"],
    ["/smart-maintenance?tab=tiers&inspect=table-1", "/decisions?tab=recommendations&inspect=table-1"],
    ["/optimize", "/decisions?tab=recommendations"],
    ["/impact", "/cost?tab=storage"],
    ["/impact?range=7", "/cost?tab=storage&range=7"],
    ["/scan-cost", "/cost?tab=reads"],
    ["/cost", "/cost?tab=storage"],
    ["/cost?tab=overview", "/cost?tab=storage"],
    ["/cost?tab=overview&scope=prod", "/cost?tab=storage&scope=prod"],
    ["/cost?tab=overview#opportunities", "/cost?tab=storage#opportunities"],
    ["/cost?tab=s3-requests", "/cost?tab=requests"],
    ["/cost?tab=aws-integrations", "/cost?tab=storage"],
    ["/cost/aws", "/settings/connections?tab=lakehouses"],
    ["/aws", "/settings/connections?tab=lakehouses"],
    ["/aws?connection=src-1", "/settings/connections?connection=src-1&tab=lakehouses"],
    ["/cost?tab=storage", null],
    ["/cost?tab=requests", null],
    ["/cost?tab=impact", "/cost?tab=storage"],
    ["/cost?tab=reads", null],
    ["/cost?tab=aws", "/cost?tab=storage"],
    ["/similarity", "/insights?tab=duplicates"],
    ["/similarity?cluster=1", "/insights?tab=duplicates&cluster=1"],
    ["/anomalies", "/insights?tab=shared-paths"],
    ["/queries", "/insights?tab=queries"],
    ["/queries?hours=24&engine=trino", "/insights?tab=queries&hours=24&engine=trino"],
    ["/governance?tab=triage", "/tables?view=scan-failed&notice=governance-triage"],
    ["/governance?tab=attention", "/tables?view=scan-failed&notice=governance-triage"],
    ["/governance?tab=signals", "/decisions?tab=recommendations&notice=governance-signals"],
    ["/governance?tab=signals#forecast", "/cost?tab=storage#forecast"],
    ["/governance?tab=signals#lineage", "/decisions?tab=recommendations&notice=governance-lineage#lineage"],
    ["/governance?tab=intelligence", "/decisions?tab=recommendations&notice=governance-signals"],
    ["/governance?tab=access", "/settings/access"],
    ["/governance?tab=access&policy=abc", "/settings/access?policy=abc"],
    ["/governance?tab=policies", "/settings/access"],
    ["/governance?tab=ownership", "/tables?notice=governance-ownership"],
    ["/governance?tab=inventory", "/tables?notice=governance-ownership"],
    ["/governance", "/tables?view=scan-failed&notice=governance-triage"],
    ["/governance?tab=unknown&selected=1", "/tables?view=scan-failed&notice=governance-unknown&legacyTab=unknown&selected=1"],
    ["/connections?tab=sources", "/settings/connections?tab=lakehouses"],
    ["/connections?tab=sources&edit=source&editId=abc", "/settings/connections?tab=lakehouses&edit=source&editId=abc"],
    ["/connections?tab=destinations", "/settings/connections?tab=engines"],
    ["/connections?tab=capabilities", "/settings/connections?tab=capabilities"],
    ["/connections?tab=ai-providers", "/settings/connections?tab=ai-providers"],
    ["/connections?tab=slack", "/settings/connections?tab=slack"],
    ["/connections?tab=ops", "/settings/admin"],
    ["/connections", "/settings/connections?tab=lakehouses"],
    ["/settings/connections?tab=sources", "/settings/connections?tab=lakehouses"],
    ["/settings/connections?tab=destinations", "/settings/connections?tab=engines"],
    ["/settings/connections?tab=lakehouses", null],
    ["/settings/connections?tab=ops", "/settings/admin"],
    ["/skillsets", "/settings/ai?tab=skillsets"],
    ["/ai", "/settings/ai?notice=ai-moved"],
    ["/ai?tab=experimental#diag", "/settings/ai?tab=experimental&notice=ai-moved#diag"],
    ["/settings", "/settings/connections?tab=lakehouses"],
    ["/attribution", null],
    ["/runs?tab=history", null],
    ["/runs?tab=runs", "/runs?tab=history"],
    ["/decisions?tab=recommendations", null],
    ["/decisions?tab=approvals", null],
    ["/decisions?tab=skipped", null],
    ["/insights", null],
    ["/insights?tab=duplicates", null]
  ];

  it.each(cases)("%s -> %s", (input, output) => {
    expectRedirect(input, output);
  });

  it("does not loop tables, runs, or home back to the old paths", () => {
    expect(resolveLegacyRedirect(parse("/tables"))).toBeNull();
    expect(resolveLegacyRedirect(parse("/"))?.pathname).not.toBe("/health");
    const runs = resolveLegacyRedirect(parse("/runs"));
    expect(runs?.pathname).toBe("/runs");
    expect(runs?.pathname).not.toBe("/operations");
    expect(resolveLegacyRedirect(parse("/runs/run-1"))).toBeNull();
  });
});

describe("table detail tab aliases", () => {
  it("translates §4.2 without applying them as redirects", () => {
    expect(TABLE_DETAIL_TAB_ALIASES).toEqual({
      structure: "files",
      "health-quality": "quality",
      activity: "maintenance",
      lineage: "usage"
    });
    expect(translateTableDetailTab("overview")).toBe("overview");
    expect(translateTableDetailTab("structure")).toBe("files");
    expect(translateTableDetailTab("health-quality")).toBe("quality");
    expect(translateTableDetailTab("activity")).toBe("maintenance");
    expect(translateTableDetailTab("maintenance")).toBe("maintenance");
    expect(translateTableDetailTab("lineage")).toBe("usage");
    expect(translateTableDetailTab("snapshots")).toBe("snapshots");
    expect(translateTableDetailTab("cost")).toBe("cost");
    expect(translateTableDetailTab(null)).toBeNull();
  });
});

describe("runs nested paths", () => {
  it("matches /runs/jobs/:jobId before /runs/:runId", () => {
    expect(RUNS_NESTED_PATHS.indexOf("/runs/jobs/:jobId")).toBeLessThan(RUNS_NESTED_PATHS.indexOf("/runs/:runId"));
    expect(firstRunsNestedMatch("/runs/jobs/job-1")).toBe("/runs/jobs/:jobId");
    expect(firstRunsNestedMatch("/runs/workflows/preset-1")).toBe("/runs/workflows/:presetId");
    expect(firstRunsNestedMatch("/runs/scanner")).toBe("/runs/scanner");
    expect(firstRunsNestedMatch("/runs/run-1")).toBe("/runs/:runId");
    const ranked = RUNS_NESTED_PATHS.find((pattern) => matchPath({ path: pattern, end: true }, "/runs/jobs/job-1"));
    expect(ranked).toBe("/runs/jobs/:jobId");
  });
});
