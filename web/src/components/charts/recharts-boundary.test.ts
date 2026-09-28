import { describe, expect, it } from "vitest";
import allowlistText from "./recharts-allowlist.txt?raw";

const sources = import.meta.glob("../../**/*.{ts,tsx,js,jsx}", {
  eager: true,
  import: "default",
  query: "?raw"
}) as Record<string, string>;

export function importsRecharts(source: string): boolean {
  const stripped = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  return (
    /(?:from\s+|import\s*\(\s*|require\s*\(\s*)["']recharts(?:\/[^"']*)?["']/.test(stripped) ||
    /\bimport\s+["']recharts(?:\/[^"']*)?["']/.test(stripped)
  );
}

function srcRelative(key: string): string {
  const stack = ["components", "charts"];
  for (const part of key.replace(/\\/g, "/").split("/")) {
    if (part === "..") stack.pop();
    else if (part !== "." && part !== "") stack.push(part);
  }
  return stack.join("/");
}

function allowlist(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("#"))
    .sort();
}

function importerPaths(): string[] {
  return Object.entries(sources)
    .map(([key, source]) => ({ rel: srcRelative(key), source }))
    .filter(({ rel, source }) => !rel.startsWith("components/charts/") && importsRecharts(source))
    .map(({ rel }) => rel)
    .sort();
}

describe("recharts import boundary", () => {
  it("detects imports and ignores mentions", () => {
    expect(importsRecharts('import { Bar } from "recharts";')).toBe(true);
    expect(importsRecharts("import { Bar } from 'recharts';")).toBe(true);
    expect(importsRecharts('export { Bar } from "recharts";')).toBe(true);
    expect(importsRecharts('const load = () => import("recharts");')).toBe(true);
    expect(importsRecharts('const lib = require("recharts");')).toBe(true);
    expect(importsRecharts('import { Bar } from "recharts/es6/chart/Bar";')).toBe(true);
    expect(importsRecharts('import {\n  Bar\n} from "recharts";')).toBe(true);
    expect(importsRecharts('// from "recharts"\nconst name = "recharts";')).toBe(false);
    expect(importsRecharts('/* from "recharts" */\nconst name = "recharts";')).toBe(false);
    expect(importsRecharts('className="recharts-default-tooltip"')).toBe(false);
  });

  it("fails when a new importer appears outside components/charts", () => {
    expect(allowlistText.toLowerCase()).toContain("shrinks");
    const allowed = allowlist(allowlistText);
    const importers = importerPaths();
    expect(importers).toContain("components/ui/chart-panel.tsx");
    expect(allowed.filter((rel) => rel.startsWith("components/charts/"))).toEqual([]);
    expect(importers).toEqual(allowed);
  });
});
