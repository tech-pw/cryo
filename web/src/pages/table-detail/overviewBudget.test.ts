import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const source = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), "useTableDetail.ts"), "utf8");

describe("overview request budget", () => {
  it("keeps cold overview queries at or under ten", () => {
    const blocks = source.split("useQuery({").slice(1);
    const alwaysOn = blocks.filter((block) => !block.includes("enabled:"));
    expect(alwaysOn.length).toBeLessThanOrEqual(10);
  });
});
