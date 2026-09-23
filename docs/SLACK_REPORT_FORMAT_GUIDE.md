# Slack Report Format Guide

> A comprehensive guide for building effective daily reports, digests, and automated messages in Slack.
> Compiled from official Slack documentation, Block Kit reference, and industry best practices.

---

## Table of Contents

1. [Slack Formatting Fundamentals](#1-slack-formatting-fundamentals)
2. [Block Kit Reference](#2-block-kit-reference)
3. [Daily Report Structure](#3-daily-report-structure)
4. [Best Practices](#4-best-practices)
5. [Anti-Patterns to Avoid](#5-anti-patterns-to-avoid)
6. [Templates](#6-templates)
7. [Technical Limits](#7-technical-limits)
8. [Sources](#8-sources)

---

## 1. Slack Formatting Fundamentals

### mrkdwn vs Markdown

Slack uses **mrkdwn** (not Markdown). The syntax differences cause most formatting frustrations.

| Format | Standard Markdown | Slack mrkdwn |
|--------|-------------------|--------------|
| **Bold** | `**text**` | `*text*` |
| *Italic* | `*text*` or `_text_` | `_text_` |
| ~~Strike~~ | `~~text~~` | `~text~` |
| `Code` | `` `code` `` | `` `code` `` |
| Code block | ` ```code``` ` | ` ```code``` ` |
| Blockquote | `> text` | `> text` |
| Link | `[label](url)` | `<url\|label>` |
| User mention | N/A | `<@U12345678>` |
| Channel mention | N/A | `<#C12345678>` |

### What mrkdwn Does NOT Support

- Headings (`#`, `##`, `###`) — use `header` blocks instead
- Tables — use `section.fields` or Table Block via API
- Images inline — use `image` blocks
- Nested formatting (bold + italic)
- Text color
- Horizontal rules (`---`) — use `divider` blocks

### Emoji

Use colon format: `:emoji_name:` (e.g., `:rocket:`, `:warning:`, `:white_check_mark:`)

Common status emojis for reports:
- 🟢 `:large_green_circle:` — OK / healthy / pass
- 🟡 `:large_yellow_circle:` — warning / watch
- 🔴 `:red_circle:` — critical / fail
- ⬆️ `:arrow_up:` or `:chart_with_upwards_trend:` — increase
- ⬇️ `:arrow_down:` or `:chart_with_downwards_trend:` — decrease
- ✅ `:white_check_mark:` — completed
- ❌ `:x:` — failed / blocked

---

## 2. Block Kit Reference

Block Kit is Slack's JSON-based layout system for rich messages. Use it for automated reports.

### Core Block Types

#### Header Block
Large, bold text. Use for report titles.

```json
{
  "type": "header",
  "text": {
    "type": "plain_text",
    "text": "🧊 Cryo Daily Report — July 9, 2026"
  }
}
```

**Constraints:**
- `text.type` MUST be `plain_text` (mrkdwn not allowed)
- Max ~150 characters (longer wraps awkwardly)

#### Section Block
The workhorse block. Supports text, fields (two-column grid), and accessories.

```json
{
  "type": "section",
  "text": {
    "type": "mrkdwn",
    "text": "*Bottom line:* Fleet health improved B→B+. EMR up 14%, Trino down 25%."
  }
}
```

**With fields (two-column key-value layout):**

```json
{
  "type": "section",
  "fields": [
    { "type": "mrkdwn", "text": "*Total Tables*\n847" },
    { "type": "mrkdwn", "text": "*Health*\n823 ✓ / 24 ✗" },
    { "type": "mrkdwn", "text": "*Backlog*\n12 TB ↓8" },
    { "type": "mrkdwn", "text": "*S3 Cost*\n$415 ↑$12" }
  ]
}
```

**Constraints:**
- Max 10 fields per section
- Max 2000 chars per field text
- Max 3000 chars for section text

**With accessory (button, image, etc.):**

```json
{
  "type": "section",
  "text": { "type": "mrkdwn", "text": "View the full report" },
  "accessory": {
    "type": "button",
    "text": { "type": "plain_text", "text": "Open Dashboard" },
    "url": "https://grafana.example.com/d/abc123"
  }
}
```

#### Divider Block
Horizontal line. Use to separate sections.

```json
{
  "type": "divider"
}
```

#### Context Block
Small, muted text. Use for metadata, timestamps, sources.

```json
{
  "type": "context",
  "elements": [
    { "type": "mrkdwn", "text": "📊 <https://grafana.example.com|Grafana>" },
    { "type": "mrkdwn", "text": "📋 <https://openmetadata.example.com|OpenMetadata>" },
    { "type": "mrkdwn", "text": "Generated at 09:00 IST" }
  ]
}
```

**Constraints:**
- Max 10 elements
- Max 2000 chars total across all elements

#### Actions Block
Interactive buttons, menus, date pickers.

```json
{
  "type": "actions",
  "elements": [
    {
      "type": "button",
      "text": { "type": "plain_text", "text": "Acknowledge" },
      "style": "primary",
      "action_id": "ack_report"
    },
    {
      "type": "button",
      "text": { "type": "plain_text", "text": "Snooze 7d" },
      "action_id": "snooze_7d"
    },
    {
      "type": "button",
      "text": { "type": "plain_text", "text": "Open Incident" },
      "style": "danger",
      "action_id": "open_incident",
      "confirm": {
        "title": { "type": "plain_text", "text": "Create incident?" },
        "text": { "type": "mrkdwn", "text": "This will page the on-call team." },
        "confirm": { "type": "plain_text", "text": "Create" },
        "deny": { "type": "plain_text", "text": "Cancel" }
      }
    }
  ]
}
```

**Button styles:**
- No style (default) — gray
- `"style": "primary"` — green
- `"style": "danger"` — red

**Constraints:**
- Max 25 elements per actions block
- Max 5 actions blocks per message

#### Image Block
Display an image (chart, poster, screenshot).

```json
{
  "type": "image",
  "image_url": "https://example.com/chart.png",
  "alt_text": "Daily cost trend chart"
}
```

**Constraints:**
- URL must be publicly accessible (or Slack-hosted)
- Max 3000 chars for `alt_text`

### Always Include a `text` Fallback

Every message with blocks MUST include a top-level `text` field:

```json
{
  "channel": "C0BD3L56AEM",
  "text": "🟢 Daily Report — Fleet healthy, no action required",
  "blocks": [ /* ... */ ]
}
```

This fallback is used for:
- Push notifications
- Search results
- Screen readers
- Threads list preview

---

## 3. Daily Report Structure

> **Superseded for the Cryo daily digest (2026-09-23).** The 6-section framework below is
> retained as general guidance, but `daily_report.py::_build_slack_blocks` no longer follows
> it. The digest was cut to the counters actually tracked day to day — see
> "Shipped shape (2026-09-23)" immediately after this section. The status banner, narrated
> bottom line, anomaly list, cost/savings figures, top-unhealthy list and action items were
> all removed on request.

### The 6-Section Framework

Research shows the most effective daily reports follow this structure:

```
┌──────────────────────────────────────────────────────────────┐
│ 1. HEADER — Report title + date                              │
├──────────────────────────────────────────────────────────────┤
│ 2. STATUS — 🟢 CLEAR | 🟡 WATCH (N) | 🔴 CRITICAL (N)        │
├──────────────────────────────────────────────────────────────┤
│ 3. BOTTOM LINE — 1-2 sentences: what + why                   │
├──────────────────────────────────────────────────────────────┤
│ 4. KEY METRICS — 4-8 items in fields (two-column)            │
├──────────────────────────────────────────────────────────────┤
│ 5. ACTION ITEMS — 0-3 items with owner + due date            │
├──────────────────────────────────────────────────────────────┤
│ 6. FOOTER — Drill-down links + timestamp                     │
└──────────────────────────────────────────────────────────────┘
```

### Shipped shape (2026-09-23)

What `_build_slack_blocks` renders today, in order. Every value is a counter someone is
tracking over time; nothing is narrated or inferred.

| Section | Rows |
|---|---|
| Fleet | Total Tables · Scanned (yesterday) · Needing Compaction · Total Size |
| Maintenance Operations (yesterday) | one row per operation, succeeded / failed |
| Impact | GB Reclaimed · Successful Actions · Tables Touched · Optimize File Reduction |
| Trend | Day-over-day (vs 2 days ago) · Week-over-week (vs same day last week) |
| Grade Distribution | tables per grade |
| Files | Total Files · Small Files · Small-File Ratio · Avg File Size |

Two things worth preserving if this is ever reworked:

- **Every "yesterday" means the same window.** Maintenance counts, the scanned count and
  the impact numbers all use `impact_signals.day_bounds(now, 1)` — a full UTC calendar day.
  They previously mixed a rolling 24 hours with a calendar day and disagreed by ~900 actions
  in the same message.
- **`AuditLog.status` is `'OK'` / `'FAILED'`.** The old summary line matched on the string
  `"succeeded"`, which never appears, so it printed "0 succeeded, 0 failed" on days with
  thousands of successful actions. `_maintenance_split` maps the real vocabulary.

The digest is generated with `include_diagnostics=False`, which skips `detect_all_anomalies`
— measured at **540s** against the 12.6k-table fleet, blocking the scheduler's async tick
for nine minutes to compute a section that is no longer rendered. With it off, collection
takes ~1s.

### Section Details

#### 1. Header
- Use `header` block
- Include emoji for visual identity
- Include date in human-readable format

#### 2. Status (Headline)
- First thing readers see — answers "do I need to act?" in 1 second
- Use exactly three states:
  - 🟢 **CLEAR** — all systems normal
  - 🟡 **WATCH** (N items) — something needs attention
  - 🔴 **CRITICAL** (N items) — immediate action required
- 80% of readers stop here on good days

#### 3. Bottom Line
- One sentence = what happened
- Second sentence = why it happened (causal link)
- **Pre-compute all numbers in SQL; LLM only narrates** — eliminates hallucination
- Example: "Fleet health improved B→B+. EMR up 14% (Lagoon batch spike), Trino down 25% (replica-0 scaled in)."

#### 4. Key Metrics
- Use `section.fields` for two-column grid
- 4-8 metrics maximum — more causes cognitive overload
- Each metric shows: **Name**, **Current Value**, **Delta** (↑/↓), **Status** (🟢/🟡/🔴)
- Always compare to the **right prior**:
  - DoD (day-over-day) for operational metrics
  - WoW (week-over-week, same weekday) for cyclical metrics
- Use `%` for volume metrics, `pp` (percentage points) for rate metrics

#### 5. Action Items
- 0-3 items only — more means the digest is trying to be a task tracker
- Every item MUST have:
  - Owner (`@username`)
  - Due date (today / tomorrow / specific date)
- If no action needed, explicitly say "No action required"
- Format: `• 🔴 \`table_name\` — issue description @owner (due: date)`

#### 6. Footer
- Use `context` block (small, muted text)
- Include drill-down links to:
  - Grafana dashboard
  - Data catalog / OpenMetadata
  - Source query (Athena, Metabase)
  - Full report (if available)
- Include generation timestamp

### Optional: Interactive Buttons
Add `actions` block when:
- Acknowledgment is required
- Common follow-up actions exist (snooze, escalate, open incident)
- Skip if buttons are never clicked — dead buttons train readers to ignore

---

## 4. Best Practices

### Message Design

| Do | Don't |
|----|-------|
| Lead with status (CLEAR/WATCH/CRITICAL) | Bury the lede in paragraphs |
| Use `section.fields` for KV data | Use code blocks for tabular data |
| Pre-compute numbers in SQL | Let LLM generate numbers (hallucination risk) |
| Keep same structure every day | Change layout based on content |
| Link each metric to its source | Force readers to hunt for context |
| Use emoji consistently | Mix emoji styles randomly |
| Show delta + current value | Show current value alone |

### Timing & Distribution

| Factor | Best Practice |
|--------|---------------|
| **When** | 15-30 min before team's start time (e.g., 9:00 IST → send 8:30) |
| **Where** | Smallest channel where someone is accountable |
| **Length** | <60 second read; <400 words prose |
| **Cadence** | Daily for ops; weekly roll-up for leadership |

### Comparison Baselines

| Metric Type | Compare To | Why |
|-------------|------------|-----|
| Operational (errors, latency) | Yesterday (DoD) | Detect immediate regressions |
| Volume (traffic, cost) | Same weekday last week (WoW) | Account for weekly cycles |
| Growth (users, revenue) | Same day last month (MoM) | Account for monthly cycles |

### Direction Indicators

- **Direction emoji MUST match the actual direction**
  - ↑ with positive delta
  - ↓ with negative delta
  - One mismatch kills trust in the entire report
- Use consistent symbols across all reports
- Include both direction AND magnitude: `↑12%` not just `↑`

---

## 5. Anti-Patterns to Avoid

### Layout Anti-Patterns

| Anti-Pattern | Problem | Fix |
|--------------|---------|-----|
| Wall of `code blocks` | Unreadable on mobile; no visual hierarchy | Use `section.fields` |
| 10+ metrics | Cognitive overload | Pick 4-8 that drive decisions |
| Delta without context | "+12%" is meaningless alone | Show `current` + `delta` + `prior label` |
| Raw JSON/SQL output | Alienates non-technical readers | Summarize in prose, link to raw |
| Layout changes daily | Reader re-parses every time | Same structure every day |

### Content Anti-Patterns

| Anti-Pattern | Problem | Fix |
|--------------|---------|-----|
| LLM generates numbers | Hallucination risk; inconsistent | SQL computes, LLM narrates |
| Action items without owners | Becomes a wish list | No owner = no item |
| Charts on every update | Chart fatigue; slows scroll | Chart only when trend-shape matters |
| No fallback `text` | Broken notifications/search | Always include `text` param |
| Tables in code blocks | Unreadable on mobile | Use `section.fields` or Table Block |

### Formatting Anti-Patterns

| Anti-Pattern | Problem | Fix |
|--------------|---------|-----|
| `**bold**` (Markdown) | Shows literal asterisks | Use `*bold*` (mrkdwn) |
| `[text](url)` links | Shows raw brackets | Use `<url\|text>` |
| `## Heading` | Shows literal hashes | Use `header` block |
| Very long `header` text | Wraps awkwardly | Keep under ~150 chars |
| `mrkdwn` in header block | Error / ignored | Header only supports `plain_text` |

---

## 6. Templates

### Template A: Minimal Daily Report

```json
{
  "text": "🟢 Daily Report — All systems healthy",
  "blocks": [
    {
      "type": "header",
      "text": { "type": "plain_text", "text": "🧊 Cryo Daily Report — July 9, 2026" }
    },
    {
      "type": "section",
      "text": { "type": "mrkdwn", "text": "🟢 *CLEAR* — All systems healthy" }
    },
    {
      "type": "section",
      "text": {
        "type": "mrkdwn",
        "text": "Fleet health stable at B+. No anomalies detected. Maintenance completed 12 tables overnight."
      }
    },
    {
      "type": "section",
      "fields": [
        { "type": "mrkdwn", "text": "*Total Tables*\n847" },
        { "type": "mrkdwn", "text": "*Health*\n823 ✓ / 24 ✗" },
        { "type": "mrkdwn", "text": "*Backlog*\n12 TB" },
        { "type": "mrkdwn", "text": "*Anomalies*\n0" }
      ]
    },
    {
      "type": "divider"
    },
    {
      "type": "context",
      "elements": [
        { "type": "mrkdwn", "text": "📊 <https://grafana.example.com|Grafana> • 📋 <https://openmetadata.example.com|Catalog> • Generated 09:00 IST" }
      ]
    }
  ]
}
```

### Template B: Report with Watch Items

```json
{
  "text": "🟡 Daily Report — 2 items need attention",
  "blocks": [
    {
      "type": "header",
      "text": { "type": "plain_text", "text": "🧊 Cryo Daily Report — July 9, 2026" }
    },
    {
      "type": "section",
      "text": { "type": "mrkdwn", "text": "🟡 *WATCH* (2 items)" }
    },
    {
      "type": "section",
      "text": {
        "type": "mrkdwn",
        "text": "Fleet B→B+. EMR cost up 14% (Lagoon batch spike). Trino down 25% (replica-0 scaled in). Net cost neutral."
      }
    },
    {
      "type": "divider"
    },
    {
      "type": "section",
      "fields": [
        { "type": "mrkdwn", "text": "*Total Tables*\n847" },
        { "type": "mrkdwn", "text": "*Health*\n823 ✓ / 24 ✗ ↑3" },
        { "type": "mrkdwn", "text": "*Compaction Backlog*\n12 TB ↓8 TB" },
        { "type": "mrkdwn", "text": "*Anomalies*\n2 critical 🟡" },
        { "type": "mrkdwn", "text": "*S3 Cost/day*\n$415 ↑$12 🟡" },
        { "type": "mrkdwn", "text": "*Trino Cost/day*\n$435 ↓$144 🟢" }
      ]
    },
    {
      "type": "divider"
    },
    {
      "type": "section",
      "text": {
        "type": "mrkdwn",
        "text": "*Action Items*\n• 🟡 `events` — null-rate spike, investigate <@U04J3835Q77> (today)\n• 🟡 EMR cost — verify Lagoon batch intentional <@U07NKREPW5N> (tomorrow)"
      }
    },
    {
      "type": "divider"
    },
    {
      "type": "context",
      "elements": [
        { "type": "mrkdwn", "text": "📊 <https://grafana.example.com|Grafana> • 📋 <https://openmetadata.example.com|Catalog> • 🔍 <https://athena.example.com|Athena>" }
      ]
    },
    {
      "type": "actions",
      "elements": [
        {
          "type": "button",
          "text": { "type": "plain_text", "text": "✓ Acknowledge" },
          "style": "primary",
          "action_id": "ack"
        },
        {
          "type": "button",
          "text": { "type": "plain_text", "text": "Snooze 7d" },
          "action_id": "snooze"
        }
      ]
    }
  ]
}
```

### Template C: Critical Alert

```json
{
  "text": "🔴 CRITICAL — Immediate action required",
  "blocks": [
    {
      "type": "header",
      "text": { "type": "plain_text", "text": "🔴 CRITICAL — Cryo Alert" }
    },
    {
      "type": "section",
      "text": {
        "type": "mrkdwn",
        "text": "*`orders` table health dropped to F*\n\nNull-rate spiked from 0.1% → 47% in the last 2 hours. Upstream CDC pipeline may have schema drift."
      }
    },
    {
      "type": "section",
      "fields": [
        { "type": "mrkdwn", "text": "*Table*\n`iceberg.gold.orders`" },
        { "type": "mrkdwn", "text": "*Health*\nF (was B+)" },
        { "type": "mrkdwn", "text": "*Null Rate*\n47% ↑46.9pp" },
        { "type": "mrkdwn", "text": "*Last Good Snapshot*\n2026-07-09 06:00 IST" }
      ]
    },
    {
      "type": "section",
      "text": {
        "type": "mrkdwn",
        "text": "*Immediate Action*\n<@U04J3835Q77> — investigate CDC pipeline\n<@U098VTVBBJS> — pause downstream dbt jobs"
      }
    },
    {
      "type": "divider"
    },
    {
      "type": "context",
      "elements": [
        { "type": "mrkdwn", "text": "📊 <https://grafana.example.com/d/orders|Table Dashboard> • 🔍 <https://openmetadata.example.com/table/orders|Lineage>" }
      ]
    },
    {
      "type": "actions",
      "elements": [
        {
          "type": "button",
          "text": { "type": "plain_text", "text": "🚨 Open Incident" },
          "style": "danger",
          "action_id": "open_incident",
          "confirm": {
            "title": { "type": "plain_text", "text": "Create PagerDuty incident?" },
            "text": { "type": "mrkdwn", "text": "This will page the data-platform on-call." },
            "confirm": { "type": "plain_text", "text": "Create" },
            "deny": { "type": "plain_text", "text": "Cancel" }
          }
        },
        {
          "type": "button",
          "text": { "type": "plain_text", "text": "✓ Acknowledge" },
          "style": "primary",
          "action_id": "ack"
        }
      ]
    }
  ]
}
```

---

## 7. Technical Limits

### Block Kit Limits

| Limit | Value |
|-------|-------|
| Max blocks per message | 50 |
| Max `section.text` chars | 3000 |
| Max `section.fields` | 10 |
| Max chars per field | 2000 |
| Max `header.text` chars | ~150 (recommended) |
| Max `context` elements | 10 |
| Max `context` total chars | 2000 |
| Max buttons per `actions` | 25 |
| Max `actions` blocks per message | 5 |
| Max button label chars | 75 |

### Table Block Limits (API only)

| Limit | Value |
|-------|-------|
| Max rows | 100 |
| Max columns | 20 |
| Cell content | Plain text only (no mrkdwn) |
| Merged cells | Not supported |

### Message Limits

| Limit | Value |
|-------|-------|
| Max message text | 40,000 chars |
| Max attachments | 100 |
| Max unfurled links | 5 |

---

## 8. Sources

### Official Slack Documentation
- [Block Kit Overview](https://api.slack.com/block-kit)
- [Block Kit Reference](https://docs.slack.dev/reference/block-kit)
- [Text Object Reference](https://docs.slack.dev/reference/block-kit/composition-objects/text-object)
- [Format Your Messages](https://slack.com/help/articles/202288908-Format-your-messages-in-Slack)
- [Block Kit Builder](https://app.slack.com/block-kit-builder) (interactive tool)

### Best Practices Research
- [Anatomy of a Daily Slack Update](https://chartcastr.com/blog/anatomy-of-a-daily-slack-update)
- [Automated Business Metrics Digests](https://www.kaelio.com/blog/how-to-set-up-automated-business-metrics-digests-in-slack)
- [Slack KPI Summary Format](https://adriennevermorel.com/notes/slack-kpi-summary-format/)
- [Slack Block Kit for Ops](https://devopsaitoolkit.com/blog/slack-block-kit-message-design-for-ops/)
- [Slack Notifications Best Practices](https://knock.app/manuals/slack-notifications/best-practices-for-slack-notifications)

### Formatting Deep Dives
- [Slack Formatting Masterclass: mrkdwn, Blocks, and Rich Messages](https://unmarkdown.com/blog/slack-formatting-masterclass)
- [Slack Markdown Complete Syntax Guide 2026](https://macmdviewer.com/blog/markdown-in-slack)

### Daily Digest Patterns
- [The Daily Digest Framework](https://agentfactory.panaversity.org/docs/Business-Domain-Agent-Workflows/productivity-agentic-office/the-daily-digest)
- [Daily Executive Briefing Template](https://www.basedash.com/automation-templates/daily-executive-briefing)
- [Project Delta Digest (Microsoft)](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/agent-template-project-delta-digest)

---

*Last updated: 2026-07-09*
