import { describe, expect, it } from "vitest";

import { engineImages, groupComponentBuilds, type ComponentBuild } from "./buildInfo";

const build = (over: Partial<ComponentBuild>): ComponentBuild => ({
  component: "worker",
  instance: "w-1",
  image: "registry.example/cryo@sha256:a",
  version: "1.0.0",
  git_sha: "aaaaaaa",
  built_at: null,
  spark_image: null,
  native_image: null,
  reported_at: "2026-11-12T00:00:00Z",
  ...over
});

describe("component builds", () => {
  it("collapses pods on the same build and splits a mixed rollout", () => {
    const rows = groupComponentBuilds([
      build({ instance: "w-1" }),
      build({ instance: "w-2" }),
      build({ instance: "w-3", git_sha: "bbbbbbb" })
    ]);
    expect(rows.map((r) => [r.git_sha, r.instances])).toEqual([
      ["aaaaaaa", 2],
      ["bbbbbbb", 1]
    ]);
  });

  it("lists each configured engine image once", () => {
    const images = engineImages([
      build({ spark_image: "s@1" }),
      build({ instance: "w-2", spark_image: "s@1", native_image: "n@1" })
    ]);
    expect(images).toEqual({ spark: ["s@1"], native: ["n@1"] });
  });
});
