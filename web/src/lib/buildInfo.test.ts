import { describe, expect, it } from "vitest";

import { buildSummary, DEV_VERSION, shortSha, versionsDiffer, webBuild, type ApiVersion } from "./buildInfo";

const api = (overrides: Partial<ApiVersion> = {}): ApiVersion => ({
  version: "v0.1.6",
  git_sha: "abcdef1234567890",
  built_at: "2026-10-26T10:00:00Z",
  schema_revision: "20261020_0001",
  ...overrides
});

describe("buildInfo", () => {
  it("reads the web build from Vite env and falls back to dev", () => {
    expect(webBuild({ VITE_CRYO_VERSION: "v0.1.6", VITE_CRYO_GIT_SHA: "abc" })).toEqual({
      version: "v0.1.6",
      gitSha: "abc"
    });
    expect(webBuild({ VITE_CRYO_VERSION: " ", VITE_CRYO_GIT_SHA: "" })).toEqual({ version: DEV_VERSION, gitSha: null });
  });

  it("only flags a mismatch between two real builds", () => {
    expect(versionsDiffer({ version: DEV_VERSION, gitSha: null }, api())).toBe(false);
    expect(versionsDiffer({ version: "v0.1.6", gitSha: null }, undefined)).toBe(false);
    expect(versionsDiffer({ version: "v0.1.6", gitSha: "abcdef1234567890" }, api())).toBe(false);
    expect(versionsDiffer({ version: "v0.1.6", gitSha: "0000000" }, api())).toBe(true);
    expect(versionsDiffer({ version: "v0.1.5", gitSha: null }, api({ git_sha: null }))).toBe(true);
  });

  it("summarises both builds for a bug report", () => {
    expect(shortSha("abcdef1234567890")).toBe("abcdef1");
    expect(buildSummary({ version: "v0.1.6", gitSha: "abcdef1234567890" }, api())).toBe(
      "Cryo api v0.1.6 (abcdef1), schema 20261020_0001; web v0.1.6 (abcdef1)"
    );
    expect(buildSummary({ version: DEV_VERSION, gitSha: null }, undefined)).toBe("Cryo api unknown; web dev");
  });
});
