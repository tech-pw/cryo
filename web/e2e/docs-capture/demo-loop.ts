import { execFileSync } from "node:child_process";
import { mkdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { test, type Locator, type Page } from "@playwright/test";

import { applyTheme, settlePage } from "../visual/helpers";
import { DEMO_TABLES } from "./demo-data";
import { installDemo } from "./demo-routes";

// Records the README demo loop (Tables -> health grade -> plan review -> run receipt) from the
// real UI on synthetic demo data, then encodes docs/img/demo-loop.{gif,mp4} and its poster.
// Run: npm run capture:demo-loop (needs ffmpeg on PATH, or FFMPEG=/path/to/ffmpeg).
const OUT = fileURLToPath(new URL("../../../docs/img/", import.meta.url));
const FRAMES = fileURLToPath(new URL("../../test-results/demo-loop-frames/", import.meta.url));
const FFMPEG = process.env.FFMPEG ?? "ffmpeg";
const VIEWPORT = { width: 1280, height: 800 };
const GIF_WIDTH = 880;
const GIF_FPS = 10;
const GIF_COLORS = 96;
const FPS = 15;
// Same per-asset limit as the docs site (site/scripts/build.mjs).
const MAX_ASSET = 2 * 1024 * 1024;

// Headless Chrome paints no pointer, so the loop draws its own, plus the "Demo data" badge
// every docs-site frame carries.
const OVERLAY = `
  #demo-cursor { position: fixed; left: 0; top: 0; z-index: 2147483647; pointer-events: none;
    width: 22px; height: 22px; transform: translate(640px, 420px); transition: none; }
  #demo-cursor::after { content: ""; position: absolute; left: -9px; top: -9px; width: 40px; height: 40px;
    border-radius: 50%; background: rgba(39, 122, 190, 0.28); transform: scale(0); opacity: 0;
    transition: transform 260ms ease-out, opacity 420ms ease-out; }
  #demo-cursor.down::after { transform: scale(1); opacity: 1; transition: none; }
  #demo-badge { position: fixed; right: 16px; bottom: 16px; z-index: 2147483646; pointer-events: none;
    font: 600 12px/1 "IBM Plex Sans", system-ui, sans-serif; letter-spacing: 0.01em; color: #143E60;
    background: #EAF7FD; border: 1px solid #68C1EA; border-radius: 999px; padding: 6px 10px; }
`;

const installOverlay = (page: Page) =>
  page.addInitScript((css) => {
    document.addEventListener("DOMContentLoaded", () => {
      const style = document.createElement("style");
      style.textContent = css;
      document.head.appendChild(style);
      const cursor = document.createElement("div");
      cursor.id = "demo-cursor";
      cursor.innerHTML =
        '<svg width="22" height="22" viewBox="0 0 22 22"><path d="M2 1.5L2 17.5L6.6 13.3L9.6 20L12.6 18.7L9.7 12.2L15.8 12.2Z" fill="#0B1220" stroke="#FFFFFF" stroke-width="1.4" stroke-linejoin="round"/></svg>';
      document.body.appendChild(cursor);
      const badge = document.createElement("div");
      badge.id = "demo-badge";
      badge.textContent = "Demo data";
      document.body.appendChild(badge);
      window.addEventListener("mousemove", (e) => (cursor.style.transform = `translate(${e.clientX - 2}px, ${e.clientY - 1}px)`), true);
      window.addEventListener("mousedown", () => cursor.classList.add("down"), true);
      window.addEventListener("mouseup", () => cursor.classList.remove("down"), true);
    });
  }, OVERLAY);

const ease = (t: number) => (t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2);

/**
 * Frame-by-frame recorder: every motion step is a screenshot on a fixed timeline, and holds
 * stretch the last frame. Pacing is set here, not by how fast the machine renders.
 */
class Recorder {
  private frames: Array<{ file: string; seconds: number }> = [];
  private pointer = { x: 900, y: 300 };
  readonly marks: Record<string, number> = {};

  constructor(private readonly page: Page) {}

  get elapsed(): number {
    return this.frames.reduce((n, f) => n + f.seconds, 0);
  }

  mark(name: string): void {
    this.marks[name] = this.elapsed;
  }

  async start(): Promise<void> {
    await this.page.mouse.move(this.pointer.x, this.pointer.y);
    await this.page.waitForTimeout(300);
    await this.shot();
  }

  async shot(): Promise<void> {
    const file = join(FRAMES, `${String(this.frames.length).padStart(5, "0")}.png`);
    await this.page.screenshot({ path: file, caret: "hide" });
    this.frames.push({ file, seconds: 1 / FPS });
  }

  hold(seconds: number): void {
    this.frames[this.frames.length - 1].seconds += seconds;
  }

  async glide(target: Locator, seconds = 0.7): Promise<void> {
    const box = await target.boundingBox();
    if (!box) throw new Error("glide target has no box");
    const from = this.pointer;
    const to = { x: box.x + Math.min(box.width / 2, 48), y: box.y + box.height / 2 };
    const steps = Math.max(2, Math.round(seconds * FPS));
    for (let i = 1; i <= steps; i++) {
      const e = ease(i / steps);
      await this.page.mouse.move(from.x + (to.x - from.x) * e, from.y + (to.y - from.y) * e);
      await this.shot();
    }
    this.pointer = to;
  }

  /** Glides to `target` and clicks it; `ready` waits for the UI to answer before the next frame. */
  async click(target: Locator, ready: () => Promise<unknown>, seconds = 0.7): Promise<void> {
    await this.glide(target, seconds);
    this.hold(0.15);
    await this.page.mouse.down();
    await this.shot();
    this.hold(0.08);
    await this.page.mouse.up();
    await ready();
    await settlePage(this.page);
    await this.page.waitForTimeout(450);
    await this.shot();
  }

  /** Wheel-scrolls the app's own scroll container until `target` sits `offset` px from the top. */
  async scrollTo(target: Locator, offset = 16, seconds = 1): Promise<void> {
    const box = await target.boundingBox();
    if (!box) throw new Error("scroll target has no box");
    const distance = box.y - offset;
    const steps = Math.max(2, Math.round(seconds * FPS));
    let done = 0;
    for (let i = 1; i <= steps; i++) {
      const next = Math.round(distance * ease(i / steps));
      await this.page.mouse.wheel(0, next - done);
      done = next;
      await this.page.waitForTimeout(40);
      await this.shot();
    }
  }

  encode(): void {
    const list = this.frames.map((f) => `file '${f.file}'\nduration ${f.seconds.toFixed(4)}`);
    list.push(`file '${this.frames[this.frames.length - 1].file}'`);
    const concat = join(FRAMES, "frames.txt");
    writeFileSync(concat, list.join("\n"));
    const input = ["-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", concat];
    const scale = (width: number) => `scale=${width}:-2:flags=lanczos`;
    execFileSync(FFMPEG, [
      ...input,
      "-vf", `fps=30,${scale(VIEWPORT.width)},format=yuv420p`,
      "-c:v", "libx264", "-preset", "veryslow", "-crf", "24", "-tune", "stillimage", "-movflags", "+faststart", "-an",
      join(OUT, "demo-loop.mp4")
    ]);
    execFileSync(FFMPEG, [
      ...input,
      "-vf",
      `fps=${GIF_FPS},${scale(GIF_WIDTH)},split[a][b];[a]palettegen=max_colors=${GIF_COLORS}:stats_mode=diff[p];[b][p]paletteuse=dither=none:diff_mode=rectangle`,
      "-loop", "0",
      join(OUT, "demo-loop.gif")
    ]);
    execFileSync(FFMPEG, [
      "-v", "error", "-y", "-ss", (this.marks.poster ?? 0).toFixed(2), "-i", join(OUT, "demo-loop.mp4"),
      "-frames:v", "1", "-update", "1", join(OUT, "demo-loop-poster.png")
    ]);
  }
}

test("demo loop", async ({ page }) => {
  test.setTimeout(600_000);
  rmSync(FRAMES, { recursive: true, force: true });
  mkdirSync(FRAMES, { recursive: true });
  mkdirSync(OUT, { recursive: true });
  const unhandled = new Set<string>();
  await page.setViewportSize(VIEWPORT);
  await applyTheme(page, "light");
  await installDemo(page, unhandled);
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await installOverlay(page);

  // Warm Vite's lazy chunks so no loading state is recorded.
  const table = `/tables/${DEMO_TABLES[0].id}`;
  await page.goto(`${table}?tab=maintenance`);
  await page.getByRole("button", { name: /Add recommended/ }).waitFor({ timeout: 240_000 });
  await page.goto(table);
  await page.getByText("What's pulling the score down").first().waitFor({ timeout: 240_000 });
  await page.goto("/tables");
  const eventsLink = page.getByRole("link", { name: "analytics.events", exact: true });
  await eventsLink.waitFor({ timeout: 240_000 });
  await settlePage(page);
  page.setDefaultTimeout(30_000);
  const rec = new Recorder(page);
  await rec.start();

  // 1. Tables: every table graded, worst first.
  rec.hold(0.6);
  await rec.scrollTo(page.locator("#table-explorer"), 8, 0.9);
  rec.hold(1.2);
  await rec.click(eventsLink, () => page.getByText("What's pulling the score down").first().waitFor(), 0.8);

  // 2. The table's grade and the findings behind it.
  rec.mark("grade");
  rec.hold(0.8);
  await rec.glide(page.getByText("Small-file fragmentation").first(), 0.7);
  rec.mark("poster");
  rec.hold(1.4);
  await rec.glide(page.getByText("Snapshot retention buildup").first(), 0.4);
  rec.hold(1.2);

  // 3. Plan: add the recommended operations and review the ordered run.
  const addRecommended = page.getByRole("button", { name: /Add recommended/ });
  await rec.click(page.getByRole("tab", { name: "Maintenance" }), () => addRecommended.waitFor(), 0.6);
  rec.hold(0.4);
  await rec.scrollTo(addRecommended.locator("xpath=ancestor::section[1]"), 16, 0.9);
  await rec.click(addRecommended, () => page.getByRole("button", { name: "Review & run" }).waitFor(), 0.6);
  rec.hold(0.6);
  const dialog = page.getByRole("dialog");
  await rec.click(page.getByRole("button", { name: "Review & run" }), () => dialog.getByText("Engine and settings come from").waitFor(), 0.7);
  rec.mark("plan");
  rec.hold(0.5);
  await rec.glide(dialog.locator(".td-cart-steps li").nth(1), 0.6);
  rec.hold(2.4);
  await rec.click(dialog.getByRole("button", { name: "Keep editing" }), () => dialog.waitFor({ state: "hidden" }), 0.5);

  // 4. Receipt: what the last run did, step by step, with before and after.
  const cart = page.getByRole("region", { name: "Run cart" });
  await rec.click(cart.getByRole("button", { name: "Clear" }), () => cart.waitFor({ state: "hidden" }), 0.5);
  await rec.glide(page.locator("#maintenance-runs h2"), 0.5);
  await rec.scrollTo(page.getByText("Before this run", { exact: false }).first(), 300, 1.2);
  rec.mark("receipt");
  rec.hold(0.5);
  await rec.glide(page.getByText("Before this run", { exact: false }).first(), 0.7);
  rec.hold(1.6);
  await rec.glide(page.getByText("Operations & statements").first(), 0.7);
  rec.hold(2.6);

  if (unhandled.size) console.log("[demo-loop] unhandled:", [...unhandled].join(", "));
  rec.encode();
  for (const name of ["demo-loop.gif", "demo-loop.mp4", "demo-loop-poster.png"]) {
    const size = statSync(join(OUT, name)).size;
    if (size > MAX_ASSET) throw new Error(`docs/img/${name} is ${(size / 1048576).toFixed(2)} MB, over the 2 MB budget`);
  }
  console.log(`[demo-loop] ${rec.elapsed.toFixed(1)} s, marks:`, JSON.stringify(rec.marks));
});
