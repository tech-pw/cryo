# UX improvement loop (design doc — not currently running)

**Status:** designed and proven with one manual iteration on the `ux-improvement-loop`
branch (commit `7778328`). Not scheduled, not running. This doc exists so it can be
started deliberately later, exactly as designed, rather than re-derived from scratch.

## What it is

A self-paced, autonomous loop (via Claude Code's `/loop` skill) that repeatedly finds and
fixes one small, real UX problem in Cryo's frontend per iteration — grounded in this
project's own written design philosophy, not generic AI taste.

## Why this shape, specifically

Research into AI agent loops (see below) surfaces a consistent risk for any
*unattended, repeating* code-changing loop: left to its own judgment across many
iterations, it drifts toward generic "engaging SaaS" polish — gradients, motion,
decorative flourishes — because that's the statistical center of what "improve the UI"
means across the training distribution. For Cryo specifically that drift is actively
wrong: **[`PRODUCT.md`](../PRODUCT.md)** and **[`DESIGN.md`](../DESIGN.md)** already
state the opposite brand intent explicitly — *"Calm, exact, and operational... no
decorative noise"* — and DESIGN.md has a literal bans list (no gradients, no
glassmorphism, no decorative motion, no hero-metric flourishes). So the loop's prompt
has to quote those documents directly on every iteration, not just gesture at "this
project's style."

Sources consulted: [Loop Engineering (2026)](https://agentshortlist.com/articles/loop-engineering),
[Agentic Loops: ReAct to Loop Engineering](https://datasciencedojo.com/blog/agentic-loops-explained-from-react-to-loop-engineering-2026-guide/),
[What Is an AI Agent Loop (Pexo)](https://pexo.ai/blog/what-is-an-ai-agent-loop-2316),
[AddyOsmani: Self-Improving Coding Agents](https://addyosmani.com/blog/self-improving-agents/),
[MindStudio: Loop Engineering for coding agents](https://www.mindstudio.ai/blog/what-is-loop-engineering-ai-coding-agents),
[Ralph — autonomous PRD-completion loop](https://github.com/snarktank/ralph).

Recurring themes that shaped the design below: start with the simplest loop that works;
define exit conditions and hard iteration caps *before* running, not after; the best
autonomous workflows still have 2–3 human checkpoints, since fully unattended loops are
exactly where unnoticed "AI slop" accumulates; frontend loops specifically need to verify
changes in the real running app, not just judge their own diff.

## Safety boundaries (decided, not defaults)

- **Branch-only.** All commits land on a dedicated `ux-improvement-loop` branch. The loop
  never pushes, never touches `main`, never opens a PR unless explicitly asked. This
  repo has real concurrent human/agent activity on `main` — an autonomous loop pushing
  there unsupervised is the wrong risk tradeoff.
- **One change per iteration.** Small, reviewable diffs only — no batching multiple
  fixes into one commit.
- **Hard stop at 8 iterations** without a human check-in.
- **Stops immediately, doesn't push through, if**: tests fail and can't be fixed
  in-scope, or the next change would touch anything outside `web/` (backend,
  migrations, CI config) — those require a human decision, not a UX-loop judgment call.

## Per-iteration workflow

1. Pick **one** page or component under `web/src/pages/` or `web/src/components/` not
   yet covered this loop.
2. Run the `audit` or `critique` skill against it, explicitly scored against
   `PRODUCT.md`/`DESIGN.md` — not generic best practice. In autonomous mode, the loop
   self-selects the single best finding by its own value/risk ranking rather than
   pausing for the skill's normal interactive question phase (that phase assumes a human
   is present to answer).
3. Pick the single highest-value, lowest-risk finding from that critique.
4. Implement it using the matching skill — `clarify` for copy, `arrange` for
   spacing/hierarchy, `harden` for edge cases, `normalize` for drift from the design
   tokens, `distill`/`typeset` where relevant. **Never** `delight`, `bolder`,
   `overdrive`, `animate`, or `colorize` — these directly conflict with Cryo's "no
   decorative noise" brand personality and are excluded from the loop's toolset on
   purpose, not by oversight.
5. Verify in the real running app (dev server at `http://127.0.0.1:5173`, log in as
   `e2e-operator`) via Playwright — not just a visual read of the diff.
6. Run `tsc --noEmit` and the relevant `vitest` file for the touched component. (No
   separate ESLint config exists in this repo's frontend as of this writing — `tsc` +
   `vitest` + the live browser check is the full verification surface currently
   available.)
7. Commit to `ux-improvement-loop` with a message noting `iteration N/8`.
8. Report a one-paragraph summary, then self-schedule the next wake-up (~1200–1800s
   spacing — paced so each change stays visible before the next lands, not because
   anything external is being polled).

## Proof-of-concept: iteration 1 (already done)

Commit `7778328` on `ux-improvement-loop`: `Runs.tsx`'s page subtitle read *"Realized
savings will surface here once workers record `audit_log.actual_bytes_freed`"* — a raw
Postgres column name leaking into operator-facing copy, the inverse of DESIGN.md's
"concrete nouns and verbs over marketing phrasing" guidance. Fixed to plain language,
verified live via Playwright, `tsc` clean, existing `Runs.test.tsx` still passes.

## How to actually start it

Run, in an active session, from the `ux-improvement-loop` branch (or let the loop create
it):

```
/loop no fixed interval, self-pace. Task: autonomously improve Cryo's frontend UX, one
focused change per iteration, on the dedicated `ux-improvement-loop` branch only (never
push to main, never open a PR unless asked).

Ground every decision in /Users/aamir/Projects/Cryo/PRODUCT.md and
/Users/aamir/Projects/Cryo/DESIGN.md — read them fresh each iteration if unsure. Cryo is
a calm, exact, operational infrastructure tool for data platform engineers/SREs (Iceberg
table maintenance) — the brand personality is explicitly "no decorative noise," and
DESIGN.md has an explicit bans list (no gradients, no glassmorphism, no decorative
motion, no delight/hero-metric flourishes). Do not use the `delight`, `bolder`,
`overdrive`, `animate`, or `colorize` skills — they conflict with this product's
philosophy. Prefer `audit`, `critique`, `clarify`, `arrange`, `distill`, `harden`,
`normalize`, `typeset`.

Each iteration:
1. Pick ONE page/component under web/src/pages/ or web/src/components/ not yet covered
   this loop (keep a mental rotation; iteration 1 covered Runs.tsx).
2. Run `audit` or `critique` against it, scored against PRODUCT.md/DESIGN.md
   specifically. Self-select the single best finding rather than stopping for the
   skill's normal interactive question phase.
3. Pick the single highest-value, lowest-risk finding. Small diff only.
4. Implement it with the matching skill, staying inside DESIGN.md's rules.
5. Verify in the real running app (Playwright), not just a visual diff read.
6. Run tsc --noEmit and the relevant vitest file.
7. Commit to ux-improvement-loop with a message noting iteration N/8. Never push, never
   touch main, never open a PR.
8. Report a one-paragraph summary, then self-schedule the next wake-up (~1200-1800s).

Stop and ask the user rather than continuing if: tests fail and you can't fix them
in-scope, you've done 8 iterations total without a human check-in, or you're about to
touch anything outside web/ (backend, migrations, CI config).
```

## Open questions for whoever starts it next

- Whether to raise the 8-iteration cap once the pattern's been trusted for a while.
- Whether to eventually open one cumulative PR against `ux-improvement-loop` → `main`
  once a batch of iterations looks good, rather than reviewing the branch manually.
- Whether a cron-based `/schedule` routine (survives closing the session) is worth
  switching to once this has proven itself in-session first.
