import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

type TokenMap = Record<string, string>;

const css = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), "tokens.css"), "utf8");

const stripComments = (source: string): string => source.replace(/\/\*[\s\S]*?\*\//g, "");

const extractBlocks = (source: string): { selector: string; body: string }[] => {
  const stripped = stripComments(source);
  const blocks: { selector: string; body: string }[] = [];
  let index = 0;
  while (index < stripped.length) {
    const open = stripped.indexOf("{", index);
    if (open === -1) break;
    const selector = stripped.slice(index, open).trim();
    let depth = 1;
    let cursor = open + 1;
    while (cursor < stripped.length && depth > 0) {
      if (stripped[cursor] === "{") depth += 1;
      else if (stripped[cursor] === "}") depth -= 1;
      cursor += 1;
    }
    if (selector && !selector.startsWith("@")) {
      blocks.push({ selector, body: stripped.slice(open + 1, cursor - 1) });
    }
    index = cursor;
  }
  return blocks;
};

const parseDeclarations = (body: string): TokenMap => {
  const declarations: TokenMap = {};
  for (const part of body.split(";")) {
    const splitAt = part.indexOf(":");
    if (splitAt === -1) continue;
    const name = part.slice(0, splitAt).trim();
    const value = part.slice(splitAt + 1).trim();
    if (name.startsWith("--") && value) declarations[name] = value;
  }
  return declarations;
};

const appliesTo = (selector: string, theme: "light" | "dark"): boolean =>
  selector.split(",").some((raw) => {
    const part = raw.trim();
    if (theme === "light") return part === ":root" || part.startsWith(":root");
    return part === ":root" || part.startsWith(":root") || part.includes('[data-theme="dark"]');
  });

const collect = (theme: "light" | "dark"): TokenMap => {
  const map: TokenMap = {};
  for (const block of extractBlocks(css)) {
    if (!appliesTo(block.selector, theme)) continue;
    Object.assign(map, parseDeclarations(block.body));
  }
  return map;
};

const resolveToken = (map: TokenMap, name: string, seen = new Set<string>()): string => {
  const value = map[name];
  if (!value) throw new Error(`missing ${name}`);
  const match = value.match(/^var\(\s*(--[\w-]+)\s*\)$/);
  if (!match) return value.toLowerCase();
  if (seen.has(name)) throw new Error(`cycle at ${name}`);
  seen.add(name);
  return resolveToken(map, match[1], seen);
};

const channel = (hex: string, offset: number): number => parseInt(hex.slice(offset, offset + 2), 16);

const linear = (channelValue: number): number => {
  const srgb = channelValue / 255;
  return srgb <= 0.04045 ? srgb / 12.92 : ((srgb + 0.055) / 1.055) ** 2.4;
};

const relativeLuminance = (hex: string): number => {
  const normalized = hex.toLowerCase();
  return (
    0.2126 * linear(channel(normalized, 1)) +
    0.7152 * linear(channel(normalized, 3)) +
    0.0722 * linear(channel(normalized, 5))
  );
};

const contrast = (foreground: string, background: string): number => {
  const lighter = Math.max(relativeLuminance(foreground), relativeLuminance(background));
  const darker = Math.min(relativeLuminance(foreground), relativeLuminance(background));
  return (lighter + 0.05) / (darker + 0.05);
};

const light = collect("light");
const dark = collect("dark");
const lightValue = (name: string) => resolveToken(light, name);
const darkValue = (name: string) => resolveToken(dark, name);

describe("tokens.css Iceberg palette", () => {
  it("sets the light token table without repainting neutral-150", () => {
    expect(lightValue("--primitive-accent-500")).toBe("#277abe");
    expect(lightValue("--primitive-accent-600")).toBe("#266fa8");
    expect(lightValue("--primitive-accent-100")).toBe("#eaf7fd");
    expect(lightValue("--primitive-page")).toBe("#f5fafd");
    expect(lightValue("--color-layout-bg")).toBe("#f5fafd");
    expect(lightValue("--primitive-neutral-150")).toBe("#f3f5f8");
    expect(lightValue("--color-panel")).toBe("#f3f5f8");
    expect(lightValue("--color-surface-2")).toBe("#f3f5f8");
    expect(lightValue("--primitive-surface")).toBe("#ffffff");
    expect(lightValue("--color-card")).toBe("#ffffff");
    expect(lightValue("--primitive-chart-primary")).toBe("#277abe");
    expect(lightValue("--primitive-grade-b")).toBe("#277abe");
    expect(lightValue("--sidebar-bg")).toBe("#f5fafd");
    expect(lightValue("--sidebar-active-bg")).toBe("#eaf7fd");
    expect(lightValue("--sidebar-text")).toBe("#5c667a");
    expect(lightValue("--sidebar-text-active")).toBe("#1f5f94");
    expect(lightValue("--sidebar-hover-bg")).toBe("#eef1f6");
    expect(lightValue("--color-link")).toBe("#266fa8");
    expect(lightValue("--color-on-tint")).toBe("#1f5f94");
    expect(lightValue("--color-control-border")).toBe("#808b9e");
    expect(lightValue("--color-input")).toBe("#808b9e");
    expect(lightValue("--focus-ring-color")).toBe("#277abe");
    expect(lightValue("--color-primary")).toBe("#277abe");
    expect(lightValue("--color-primary-foreground")).toBe("#ffffff");
  });

  it("sets the dark token table", () => {
    expect(darkValue("--primitive-accent-500")).toBe("#68c1ea");
    expect(darkValue("--primitive-accent-600")).toBe("#90d4f0");
    expect(darkValue("--primitive-accent-100")).toBe("#0f2a40");
    expect(darkValue("--primitive-page")).toBe("#0b0f16");
    expect(darkValue("--color-layout-bg")).toBe("#0b0f16");
    expect(darkValue("--primitive-neutral-150")).toBe("#0e141f");
    expect(darkValue("--primitive-surface")).toBe("#0e141f");
    expect(darkValue("--primitive-chart-primary")).toBe("#68c1ea");
    expect(darkValue("--primitive-grade-b")).toBe("#68c1ea");
    expect(darkValue("--sidebar-bg")).toBe("#0e141f");
    expect(darkValue("--sidebar-active-bg")).toBe("#0f2a40");
    expect(darkValue("--sidebar-text")).toBe("#a6b2c0");
    expect(darkValue("--sidebar-text-active")).toBe("#90d4f0");
    expect(darkValue("--sidebar-hover-bg")).toBe("#141c28");
    expect(darkValue("--color-link")).toBe("#90d4f0");
    expect(darkValue("--color-on-tint")).toBe("#90d4f0");
    expect(darkValue("--color-control-border")).toBe("#808b9e");
    expect(darkValue("--focus-ring-color")).toBe("#277abe");
    expect(darkValue("--color-primary")).toBe("#68c1ea");
    expect(darkValue("--color-primary-foreground")).toBe("#0b0f16");
  });

  it("leaves success, warning, danger, and grades A/C/D/F unchanged", () => {
    expect(lightValue("--primitive-success")).toBe("#1e8e5a");
    expect(lightValue("--primitive-warning")).toBe("#a9791e");
    expect(lightValue("--primitive-danger")).toBe("#b23a2e");
    expect(lightValue("--primitive-grade-a")).toBe("#1e8e5a");
    expect(lightValue("--primitive-grade-c")).toBe("#a9791e");
    expect(lightValue("--primitive-grade-d")).toBe("#c1631b");
    expect(lightValue("--primitive-grade-f")).toBe("#b23a2e");
    expect(darkValue("--primitive-success")).toBe("#3fbe85");
    expect(darkValue("--primitive-warning")).toBe("#e0b03d");
    expect(darkValue("--primitive-danger")).toBe("#f0665a");
    expect(darkValue("--primitive-grade-a")).toBe("#3fbe85");
    expect(darkValue("--primitive-grade-c")).toBe("#e0b03d");
    expect(darkValue("--primitive-grade-d")).toBe("#f0904f");
    expect(darkValue("--primitive-grade-f")).toBe("#f0665a");
  });

  it("keeps text pairs at 4.5:1 and does not put the solid accent on frost", () => {
    expect(contrast("#277abe", "#eaf7fd")).toBeLessThan(4.5);
    expect(contrast("#277abe", "#f5fafd")).toBeLessThan(4.5);
    expect(lightValue("--color-on-tint")).not.toBe(lightValue("--primitive-accent-500"));
    expect(lightValue("--color-link")).not.toBe(lightValue("--primitive-accent-500"));

    const textPairs: [string, string][] = [
      [lightValue("--color-primary-foreground"), lightValue("--color-primary")],
      [lightValue("--primitive-white"), lightValue("--primitive-accent-600")],
      [lightValue("--color-on-tint"), lightValue("--primitive-accent-100")],
      [lightValue("--color-on-tint"), lightValue("--sidebar-active-bg")],
      [lightValue("--color-link"), lightValue("--primitive-page")],
      [lightValue("--color-link"), lightValue("--primitive-accent-100")],
      [lightValue("--color-link"), lightValue("--primitive-white")],
      [lightValue("--sidebar-text"), lightValue("--sidebar-bg")],
      [lightValue("--color-muted"), lightValue("--primitive-white")],
      [lightValue("--color-muted"), lightValue("--primitive-page")],
      [darkValue("--color-primary-foreground"), darkValue("--color-primary")],
      [darkValue("--primitive-black"), darkValue("--primitive-accent-600")],
      [darkValue("--sidebar-text-active"), darkValue("--sidebar-active-bg")],
      [darkValue("--color-link"), darkValue("--primitive-page")],
      [darkValue("--color-link"), darkValue("--primitive-surface")],
      [darkValue("--sidebar-text"), darkValue("--sidebar-bg")],
      [darkValue("--color-muted"), darkValue("--primitive-bg")]
    ];

    for (const [foreground, background] of textPairs) {
      expect(contrast(foreground, background), `${foreground} on ${background}`).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("keeps control borders at 3:1 and focus rings visible", () => {
    const borderPairs: [string, string][] = [
      [lightValue("--color-control-border"), lightValue("--primitive-white")],
      [lightValue("--color-control-border"), lightValue("--primitive-page")],
      [lightValue("--color-control-border"), lightValue("--primitive-accent-100")],
      [darkValue("--color-control-border"), darkValue("--primitive-bg")],
      [darkValue("--color-control-border"), darkValue("--primitive-surface")],
      [darkValue("--color-control-border"), darkValue("--sidebar-active-bg")]
    ];
    for (const [foreground, background] of borderPairs) {
      expect(contrast(foreground, background), `${foreground} on ${background}`).toBeGreaterThanOrEqual(3);
    }

    const focusPairs: [string, string][] = [
      [lightValue("--focus-ring-color"), lightValue("--primitive-page")],
      [lightValue("--focus-ring-color"), lightValue("--sidebar-active-bg")],
      [lightValue("--focus-ring-color"), lightValue("--primitive-white")],
      [darkValue("--focus-ring-color"), darkValue("--primitive-page")],
      [darkValue("--focus-ring-color"), darkValue("--sidebar-active-bg")],
      [darkValue("--focus-ring-color"), darkValue("--primitive-surface")]
    ];
    for (const [foreground, background] of focusPairs) {
      expect(contrast(foreground, background), `${foreground} on ${background}`).toBeGreaterThanOrEqual(3);
    }
  });
});
