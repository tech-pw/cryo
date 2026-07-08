import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const STYLE_PATH = resolve(process.cwd(), "src/styles.css");
const MAX_LINES = 2914;

const text = readFileSync(STYLE_PATH, "utf8");
const lineCount = text.split("\n").length;

if (lineCount > MAX_LINES) {
  console.error(
    `styles.css budget exceeded: ${lineCount} lines (max ${MAX_LINES}). ` +
      "Route new UI styling through design tokens instead of growing global CSS."
  );
  process.exit(1);
}

console.log(`styles.css budget check passed: ${lineCount}/${MAX_LINES} lines.`);
