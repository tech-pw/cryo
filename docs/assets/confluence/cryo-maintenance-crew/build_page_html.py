"""Build Confluence HTML body for Cryo maintenance crew page (v3 visuals)."""
from __future__ import annotations

PAGE_COLLECTION = "contentId-2304180246"

# fileId + attachment title from Confluence page 2304180246 (uploaded 2026-07-27).
ATTACHMENTS: dict[str, dict[str, str | int]] = {
    "cryo-hero-iceberg-rot.png": {
        "id": "a56ebf41-a064-49a0-b08e-640f37b146e5",
        "attachment": "cryo-hero-iceberg-rot-20260727-090509.png",
        "width": 1400,
        "height": 933,
    },
    "chart-fleet-scale.png": {
        "id": "96079b1a-5d21-4b13-94dc-9931ccd5587e",
        "attachment": "chart-fleet-scale-20260727-090534.png",
        "width": 1102,
        "height": 621,
    },
    "cryo-four-rots-infographic.png": {
        "id": "2e73ccd4-fe4c-49d6-9e5e-0c29d414e644",
        "attachment": "cryo-four-rots-infographic-20260727-090503.png",
        "width": 1400,
        "height": 933,
    },
    "cryo-architecture-flow.png": {
        "id": "c29d1b6f-9d22-4d86-b8f6-4e114d2359a5",
        "attachment": "cryo-architecture-flow-20260727-090454.png",
        "width": 1400,
        "height": 933,
    },
    "cryo-library-maintenance-crew.png": {
        "id": "3c0196c4-cd1f-425f-ae1f-739d7aec5046",
        "attachment": "cryo-library-maintenance-crew-20260727-090459.png",
        "width": 1400,
        "height": 933,
    },
    "cryo-health-grades-dashboard.png": {
        "id": "bc6be109-16eb-447c-97b6-544cdf507b15",
        "attachment": "cryo-health-grades-dashboard-20260727-090459.png",
        "width": 1400,
        "height": 933,
    },
    "chart-health-deductions.png": {
        "id": "e7342acd-661d-44df-8bed-62c884080e41",
        "attachment": "chart-health-deductions-20260727-090534.png",
        "width": 1264,
        "height": 698,
    },
    "chart-small-files-bar.png": {
        "id": "7d0fb429-e6db-480b-99b6-da2679c7bbcf",
        "attachment": "chart-small-files-bar-20260727-090534.png",
        "width": 1262,
        "height": 701,
    },
}


def embed_image(key: str, caption: str) -> str:
    meta = ATTACHMENTS[key]
    display_width = min(int(meta["width"]), 760)
    return (
        f"<figure data-type=\"media-single\" data-layout=\"center\" "
        f"data-width=\"{display_width}\" data-width-type=\"pixel\">"
        f"<div data-type=\"media\" data-media-type=\"file\" "
        f"data-id=\"{meta['id']}\" data-collection=\"{PAGE_COLLECTION}\" "
        f"data-width=\"{meta['width']}\" data-height=\"{meta['height']}\">"
        f"{meta['attachment']}</div></figure>"
        f"<p><em>{caption}</em></p>"
    )


def mermaid(code: str) -> str:
    return f"<pre><code class=\"language-mermaid\">{code.strip()}</code></pre>"


BODY_PARTS: list[str] = []

BODY_PARTS.append(
    """
<div data-type="panel-success"><p><strong>Bottom line —</strong> Iceberg gives you an excellent table format, not an automatic maintenance crew. Cryo scans <strong>13,236 tables</strong>, grades health A–F, admits work only when ROI and engine load allow, and routes rewrites to native fast-path or Spark/Trino. The incident that opens this page — <strong>1,344 delete files</strong> on one scan — is what happens when nobody is watching.</p></div>
"""
)

BODY_PARTS.append(
    """
<p>One evening, a table called <code>lms.silver_adminactions</code> quietly grew to 1,344 position-delete files. Nobody had touched it. No alert had fired. Then a routine Spark read against it started throwing <code>ConnectionPoolTimeoutException: Timeout waiting for connection from pool</code> — not from a compaction job, but from Iceberg's own delete-loader, spinning up its own internal thread pool just to stitch 1,344 delete files back together on a single scan.</p>
<p>That's not a story about one broken table. It's a story about what Apache Iceberg doesn't do for you.</p>
"""
)

BODY_PARTS.append(embed_image(
    "cryo-hero-iceberg-rot.png",
    "Day 0 vs Day 90 without maintenance — the same table quietly turns from a few clean files into metadata rot and delete-file overload.",
))

BODY_PARTS.append(
    """
<h2>The stakes: one table's rot, multiplied by 13,236</h2>
<p>We run <strong>13,236 tables</strong> on a single EKS cluster, fed by dozens of BigQuery-sourced pipelines — <code>pw_medharthi_bq</code>, <code>curiousjr_bq</code>, <code>xylem_bq</code>, <code>neet_pg_bq</code>, <code>gyaane_bq</code>, and more. If one delete-heavy table can quietly exhaust a connection pool before anyone notices, multiply that risk by 13,236, and multiply the manual toil of catching each one by however many engineers we can spare in a given week.</p>
<p><em>That's not a maintenance plan — it's an incident queue with a delay timer.</em></p>
"""
)

BODY_PARTS.append(embed_image(
    "chart-fleet-scale.png",
    "One EKS cluster, 13,236 Iceberg tables — the multiplier behind every quiet rot story.",
))

BODY_PARTS.append(
    """
<h2>What Iceberg actually gives you (and what it doesn't)</h2>
<p>Iceberg's table format is genuinely excellent: <a href="https://iceberg.apache.org/">hidden partitioning</a>, snapshot isolation, schema evolution, time travel, engine-agnostic metadata — the guarantees Netflix originally wanted when moving off Hive-style directory tables (<a href="https://tabular.io/blog/iceberg-in-modern-data-architecture/">Tabular on Iceberg in modern architecture</a>). What it doesn't give you is any promise that those things stay fast. Iceberg hands you a beautifully organized filing cabinet and leaves the filing to you.</p>
<p>Four things rot if nobody intervenes:</p>
<ul>
<li><p><strong>Small files pile up.</strong> Frequent or streaming writes create many tiny data files. Every additional file is another open/seek/plan cost at query time — read performance degrades quietly, file by file. Production guidance typically targets <strong>256–512&nbsp;MB</strong> Parquet files after compaction (<a href="https://www.dremio.com/blog/apache-iceberg-small-files-problem-causes-fixes-and-prevention/">Dremio on the small-files problem</a>).</p></li>
<li><p><strong>Delete files accumulate.</strong> Merge-on-read tables don't rewrite on update or delete — they append delete markers. Every scan has to merge those markers back against the data files. Left alone, this is exactly what took down our connection pool: 1,344 delete files, one query. See <a href="https://iceberglakehouse.com/iceberg/iceberg-merge-on-read/">merge-on-read</a> and <a href="https://lakeops.dev/blog/iceberg-delete-files-merge-on-read">delete-file overhead at read time</a> for why this scales badly.</p></li>
<li><p><strong>Snapshot history grows unbounded.</strong> No expiry means metadata (<code>metadata.json</code>, manifest lists) grows forever, planning gets slower, and storage cost climbs from data files nobody can see anymore but Iceberg still has to retain. <code>expire_snapshots</code> removes snapshot <em>metadata</em> — reclaiming storage still needs orphan cleanup (<a href="https://risingwave.com/blog/iceberg-snapshot-expiration-gc/">RisingWave on snapshot expiration vs GC</a>).</p></li>
<li><p><strong>Physical layout never matches the query pattern.</strong> Iceberg writes data in whatever order it arrives. If a table is partitioned by day but queried by month, or sorted by nothing when most filters hit one column, every scan reads far more data than it needs to — and nothing in the format itself will ever tell you that.</p></li>
</ul>
<div data-type="panel-note"><p>We felt the fourth one directly: a table's health score dropped 20 points for exactly this reason — daily partitioning on a table actually queried by month, plus a small-file fragmentation check that, we later found, didn't even account for tables with a single, perfectly-compacted file. Health scoring itself needed maintenance.</p></div>
"""
)

BODY_PARTS.append(embed_image(
    "cryo-four-rots-infographic.png",
    "Four decay modes at a glance — small files, delete markers, snapshot sprawl, and layout mismatch.",
))

BODY_PARTS.append(
    mermaid(
        """
flowchart LR
    subgraph before["Day 0 — freshly written table"]
        A1[Few files]
        A2[No deletes]
        A3[1 snapshot]
        A4[Layout matches nothing yet]
    end
    subgraph after["Day 90 — untouched"]
        B1[Thousands of small files]
        B2[Hundreds of delete files]
        B3[Unbounded snapshot history]
        B4[Layout still matches nothing]
    end
    before -- "no maintenance" --> after
    after -- "?" --> C{Query}
    C -- "slow scan, pool exhaustion, rising S3 cost" --> D[Incident]
"""
    )
)

BODY_PARTS.append(
    """
<p><em>A table left alone doesn't fail loudly. It fails as a slow, compounding tax that eventually shows up as someone else's incident.</em></p>
<h2>The maintenance playbook — documented everywhere, automated nowhere by default</h2>
<p>Iceberg ships four maintenance primitives. The <a href="https://iceberg.apache.org/docs/latest/maintenance/">official maintenance guide</a> and every serious operator blog agree on what they are — and on the fact that <strong>nothing in the format schedules or prioritizes them for you</strong> (<a href="https://iomete.com/resources/blog/iceberg-maintenance-operations">IOMETE: what Iceberg ships vs what teams still build</a>).</p>
<table>
<thead><tr><th>Operation</th><th>Spark procedure (typical)</th><th>What it fixes</th></tr></thead>
<tbody>
<tr><td><strong>Expire snapshots</strong></td><td><code>expire_snapshots</code></td><td>Old snapshot metadata; dereferences data files only held by expired snapshots</td></tr>
<tr><td><strong>Remove orphan files</strong></td><td><code>remove_orphan_files</code></td><td>Physical files in storage with no live metadata reference (failed writes, aborted jobs)</td></tr>
<tr><td><strong>Compact / optimize</strong></td><td><code>rewrite_data_files</code></td><td>Small files, delete-file accumulation, layout (binpack / sort / z-order)</td></tr>
<tr><td><strong>Rewrite manifests</strong></td><td><code>rewrite_manifests</code></td><td>Fragmented manifest metadata after heavy compaction</td></tr>
</tbody>
</table>
<div data-type="panel-warning"><p><strong>Order matters.</strong> Run <code>expire_snapshots</code> <em>before</em> <code>remove_orphan_files</code>. Expiration converts snapshot-pinned files into orphan-eligible files; cleaning orphans first misses the largest reclaimable storage category (<a href="https://lakeops.dev/blog/iceberg-orphan-files-cleanup">LakeOps orphan cleanup</a>, <a href="https://lakeops.dev/blog/automating-iceberg-table-maintenance">automating maintenance sequences</a>). Cryo's Planner preserves this order: expire → remove orphans → optimize → optimize manifests.</p></div>
"""
)

BODY_PARTS.append(
    mermaid(
        """
flowchart LR
    A[expire_snapshots] --> B[remove_orphan_files]
    B --> C[optimize / rewrite_data_files]
    C --> D[rewrite_manifests]
    A -.->|"dereferences old data files"| B
    B -.->|"reclaims S3 bytes"| C
    C -.->|"fewer files, applied deletes"| D
"""
    )
)

BODY_PARTS.append(
    """
<h3>Compaction strategies — same primitives, very different cost</h3>
<p>Compaction is not one operation. Teams pick a strategy based on write pattern, query filters, and how much compute they can spend (<a href="https://www.dremio.com/blog/compaction-in-apache-iceberg-fine-tuning-your-iceberg-tables-data-files/">Dremio compaction guide</a>, <a href="https://bigdataboutique.com/blog/iceberg-table-maintenance-4-best-practices-e914ea">BigData Boutique best practices</a>).</p>
<table>
<thead><tr><th>Strategy</th><th>What it does</th><th>When it fits</th><th>Compute</th></tr></thead>
<tbody>
<tr><td><strong>Binpack</strong></td><td>Merges small files to target size; reconciles deletes</td><td>Streaming tables, tight maintenance windows, MoR cleanup</td><td><span data-type="status" data-color="green">Lowest</span></td></tr>
<tr><td><strong>Sort</strong></td><td>Clusters data by chosen columns while compacting</td><td>Predictable filters on 1–2 columns (e.g. user_id, event_date)</td><td><span data-type="status" data-color="yellow">Medium</span></td></tr>
<tr><td><strong>Z-order</strong></td><td>Multi-column clustering for multidimensional filters</td><td>Analytics with several common filter dimensions</td><td><span data-type="status" data-color="red">Higher</span></td></tr>
</tbody>
</table>
<p>Cryo routes binpack-style compaction through the native fast path when possible; sort and z-order rewrites go to Spark/Trino. Smart Maintenance recommends layout changes from query telemetry — but a human approves before any physical rewrite runs.</p>
<div data-type="panel-info"><p><strong>Industry pattern:</strong> production lakehouses see &quot;slow drift&quot; — small files accumulate, manifests grow, snapshots pile up, delete files compound — &quot;nothing is broken, but everything costs more than it should&quot; (<a href="https://overcast.blog/9-apache-iceberg-table-maintenance-tools-you-should-know-df864ed7a6d5">survey of nine maintenance approaches</a>). That is the wound Cryo is aimed at.</p></div>
<h2>Why not cron jobs? Why not just buy a managed platform?</h2>
<p>Both are reasonable first instincts, and we tried the first one before building Cryo.</p>
<p>Raw Spark procedures — <code>rewrite_data_files</code>, <code>expire_snapshots</code>, <code>remove_orphan_files</code> — are the right primitives. But a cron job that fires <code>OPTIMIZE</code> on a schedule has no idea whether a given rewrite is worth its own compute cost, no way to avoid admitting too much work onto a shared cluster at once, and no concept of "this table's <code>retain_last</code> setting is protecting all four of its snapshots regardless of age" — a bug we hit and fixed on a real table this quarter. Cron jobs execute; they don't decide. That's the same gap every DIY Airflow-plus-dbt setup runs into — an Airflow DAG that scans, a second DAG that decides, a pile of dbt macros carrying the actual maintenance logic, and none of it sharing state or exposing a UI.</p>
<table>
<thead><tr><th></th><th><strong>Cryo</strong></th><th>DIY (Airflow + dbt)</th><th>Apache Amoro</th><th>LakeOps</th><th>Managed cloud</th></tr></thead>
<tbody>
<tr><td>Deployment</td><td><span data-type="status" data-color="green">Self-hosted K8s</span></td><td>Hand-rolled</td><td>Self-hosted</td><td>SaaS</td><td>Fully managed</td></tr>
<tr><td>Load-aware admission</td><td><span data-type="status" data-color="green">Built in</span></td><td><span data-type="status" data-color="yellow">DIY only</span></td><td><span data-type="status" data-color="yellow">Limited</span></td><td><span data-type="status" data-color="green">Yes</span></td><td>N/A</td></tr>
<tr><td>Health grading + cost</td><td><span data-type="status" data-color="green">Built in</span></td><td><span data-type="status" data-color="red">Rare</span></td><td><span data-type="status" data-color="red">No</span></td><td><span data-type="status" data-color="yellow">Partial</span></td><td>Varies</td></tr>
<tr><td>Data leaves infra</td><td><span data-type="status" data-color="green">No</span></td><td><span data-type="status" data-color="green">No</span></td><td><span data-type="status" data-color="green">No</span></td><td><span data-type="status" data-color="yellow">Telemetry SaaS</span></td><td><span data-type="status" data-color="red">Yes</span></td></tr>
<tr><td>Fleet assignment UI</td><td><span data-type="status" data-color="green">Yes</span></td><td><span data-type="status" data-color="red">No</span></td><td><span data-type="status" data-color="yellow">Limited</span></td><td><span data-type="status" data-color="green">Yes</span></td><td><span data-type="status" data-color="green">Yes</span></td></tr>
<tr><td>Engines</td><td>Native + Spark/Trino</td><td>Whatever you wired</td><td>Flink/Spark</td><td>Rust + telemetry</td><td>Platform-locked</td></tr>
</tbody>
</table>
"""
)

BODY_PARTS.append(
    mermaid(
        """
quadrantChart
    title Iceberg maintenance landscape
    x-axis Self-hosted --> Fully managed / SaaS
    y-axis Manual, hand-rolled --> Automated and intelligent
    quadrant-1 Managed and intelligent
    quadrant-2 Self-hosted and intelligent
    quadrant-3 Self-hosted and manual
    quadrant-4 Managed and manual
    Cryo: [0.15, 0.85]
    DIY Airflow+dbt: [0.1, 0.22]
    Apache Amoro: [0.2, 0.55]
    LakeOps: [0.75, 0.65]
    Managed cloud: [0.9, 0.55]
"""
    )
)

BODY_PARTS.append(
    """
<p>Every managed option is genuinely good — if you've already committed to that vendor's compute and table format. AWS Glue Table Optimizers run compaction, snapshot retention, and orphan deletion as managed background jobs for Glue-cataloged tables (<a href="https://aws.amazon.com/blogs/big-data/the-aws-glue-data-catalog-now-supports-storage-optimization-of-apache-iceberg-tables/">AWS Glue storage optimization</a>). Starburst and Dremio wrap the same SQL procedures with scheduling (<a href="https://www.starburst.io/blog/automated-table-maintenance-for-apache-iceberg/">Starburst automated maintenance</a>). They solve &quot;run maintenance&quot; — not always &quot;decide which 13,236 tables deserve compute today under live Trino load.&quot;</p>
<p>We hadn't committed to renting the whole warehouse. We wanted maintenance intelligence without telemetry leaving our infra (SaaS control planes like <a href="https://lakeops.dev/blog/iceberg-compaction-tools-compared">LakeOps</a> send query telemetry externally) and without losing multi-engine routing — native fast path plus Spark/Trino when MoR deletes or Hive catalogs demand it.</p>
<h2>Decision: Cryo</h2>
<p>Cryo is a self-hosted Iceberg maintenance platform: it continuously scans tables, scores their health, decides — with a real cost/benefit model, not a fixed schedule — what's worth fixing, and routes the actual rewrite to whichever engine can do it fastest.</p>
"""
)

BODY_PARTS.append(embed_image(
    "cryo-architecture-flow.png",
    "HLD view — Scanner and Governor in the middle; native fast-path vs Spark/Trino fallback; Smart Maintenance with human approval before layout rewrites.",
))

BODY_PARTS.append(
    mermaid(
        """
flowchart TB
    Scanner["Scanner (health scoring, small-file %, delete ratio, partition fit)"]
    Governor["Governor (ROI gate, admission control, wave-based scheduling)"]
    Native["Native fast-path engine (direct S3 + REST catalog)"]
    Spark["Spark / Trino engine (fallback — Hive Metastore, delete-heavy MOR, custom sort/z-order)"]
    SmartM["Smart Maintenance (query telemetry → layout recommendations, human-approved)"]
    Tables[(13,236 Iceberg tables)]

    Tables --> Scanner
    Scanner --> Governor
    Governor -->|"routable via native engine"| Native
    Governor -->|"needs Spark/Trino"| Spark
    Native --> Tables
    Spark --> Tables
    Scanner --> SmartM
    SmartM -->|"recommendation, pending approval"| Governor
"""
    )
)

BODY_PARTS.append(embed_image(
    "cryo-library-maintenance-crew.png",
    "Iceberg is the orderly library; Cryo is the crew that keeps re-shelving books as new ones keep arriving.",
))

BODY_PARTS.append(
    """
<p><em>Iceberg transforms our data lake into an orderly library — Cryo is the crew that keeps re-shelving it as new books keep arriving.</em></p>
<h2>Features, bucket by bucket</h2>
<p>Three groups, mirroring how the platform is actually organized internally.</p>
<section data-type="layout-two-equal">
<div data-type="column">
<p><strong>Maintenance</strong></p>
<ul>
<li><p><strong>Sources &amp; Destinations</strong> — discover from REST/Hive/Glue; maintain on whichever engine the preset targets.</p></li>
<li><p><strong>Planner</strong> — engine-agnostic chunks with GB budgets, partition caps, cooldowns, off-peak windows.</p></li>
<li><p><strong>Governor</strong> — load-aware admission, per-replica caps, round-robin across destinations.</p></li>
<li><p><strong>Presets</strong> — operations + params + engine + schedule + selection rules in one profile.</p></li>
<li><p><strong>Assignment UX</strong> — filter fleet by grade, size, drift; bulk assign presets.</p></li>
</ul>
</div>
<div data-type="column">
<p><strong>Health &amp; observability</strong></p>
<ul>
<li><p><strong>Scanner</strong> — A–F grades, compaction priority, S3 cost estimate.</p></li>
<li><p><strong>Dashboards</strong> — fleet health, audit timeline, live run console.</p></li>
<li><p><strong>Anomaly detection</strong> — drift on null-rate, size, file-count, small-file ratio.</p></li>
<li><p><strong>Cost &amp; inventory</strong> — opt-in S3/CUR ingestion for real spend numbers.</p></li>
<li><p><strong>Owner alerting</strong> — SLA breaches notify owners and subscribers.</p></li>
</ul>
</div>
</section>
"""
)

BODY_PARTS.append(embed_image(
    "cryo-health-grades-dashboard.png",
    "Fleet health at a glance — letter grades, small-file %, delete ratio, and snapshot age on every table.",
))

BODY_PARTS.append(
    """
<h2>The mechanics of a single rewrite</h2>
"""
)

BODY_PARTS.append(
    mermaid(
        """
sequenceDiagram
    participant T as Iceberg table
    participant S as Scanner
    participant G as Governor
    participant V as Plan Verifier
    participant E as Engine (native or Spark/Trino)

    T->>S: metadata scan (no live $files on hot path)
    S->>G: health grade + compaction candidate
    G->>G: ROI gate + live engine load check
    alt admitted
        G->>V: rendered plan vs live catalog
        V->>E: dispatch rewrite
        E->>T: fewer files, fresher snapshots, better layout
    else refused
        G-->>G: log refusal (QPP / load / ROI)
    end
"""
    )
)

BODY_PARTS.append(embed_image(
    "chart-health-deductions.png",
    "Health scoring is itemized — partition mismatch and delete ratio can cost 20 points each, not one opaque grade.",
))

BODY_PARTS.append(
    """
<ul>
<li><p><strong>Scanner</strong>: walks every table's metadata and computes a health score — small-file fragmentation, delete-file ratio, partition-to-query fit, snapshot age — and itemizes exactly which deduction cost how many points, not just a single opaque grade.</p></li>
<li><p><strong>Governor</strong>: the admission control layer. Before any rewrite runs, it checks a ROI gate (is the expected benefit — latency, storage, request cost — worth the compute cost of rewriting?) and admits work in waves, round-robin across destinations, so one noisy table can't starve the rest of the fleet.</p></li>
<li><p><strong>Plan Verifier</strong>: statically checks a rendered maintenance plan against the live catalog before it's ever enqueued — a proof-carrying check, not a hope-it-works dispatch.</p></li>
<li><p><strong>Native fast-path engine</strong>: talks directly to S3 and the Iceberg REST catalog for straightforward compaction and snapshot expiry — no JVM, minutes not hours.</p></li>
<li><p><strong>Spark / Trino engine</strong>: the fallback for what the fast path can't safely do — delete-heavy merge-on-read tables, Hive Metastore-backed sources, and custom sort/z-order rewrites that need a real query engine underneath them.</p></li>
<li><p><strong>Smart Maintenance</strong>: reads real query telemetry per table — which columns get filtered, how often, how selectively — and recommends a sort or z-order strategy instead of guessing. Nothing executes without a human approving it first.</p></li>
</ul>
<div data-type="panel-note"><p><em>Analogy:</em> the Governor works like airport ground control, not a single gate agent — it doesn't just check one flight's paperwork, it watches total runway load and boards planes in controlled waves so nobody stalls the whole airport. The ROI gate is closer to a renovation contractor who won't start a $10,000 kitchen remodel that will only add $2,000 to the house's value — it says no to work that doesn't pay for itself, even work it's fully capable of doing.</p></div>
<h2>AI scope — where it's real, and where it's still charts waiting for data</h2>
<p>"AI" at Cryo isn't a chatbot bolted onto a dashboard. It's a handful of narrow, purpose-built components, most of them classic optimization or statistical models rather than LLM calls — plus one place where an LLM genuinely earns its keep: turning evidence into a plain-language explanation a human can act on.</p>
<table>
<thead><tr><th>Component</th><th>What it actually is</th><th>Human in the loop?</th></tr></thead>
<tbody>
<tr><td><strong>Query Performance Prediction (QPP)</strong></td><td>Variance-aware p95 risk model; Governor refuses admission when predicted blow-up</td><td><span data-type="status" data-color="green">Automated refusal</span></td></tr>
<tr><td><strong>Plan Verifier</strong></td><td>Static proof-carrying check against live catalog</td><td><span data-type="status" data-color="green">Auto-block unsafe plans</span></td></tr>
<tr><td><strong>Layout recommendations</strong></td><td>Cost-based sort/z-order from query telemetry (WAIR-style)</td><td><span data-type="status" data-color="yellow">Pending until approved</span></td></tr>
<tr><td><strong>Union search</strong></td><td>pgvector over metadata for unionable tables</td><td><span data-type="status" data-color="blue">Search-assist</span></td></tr>
<tr><td><strong>Anomaly briefs</strong></td><td>LLM explains scan + query-pattern evidence</td><td><span data-type="status" data-color="yellow">Explanation only</span></td></tr>
<tr><td><strong>Skillsets</strong></td><td>Versioned prompt + tool bundles with outcome tracking</td><td><span data-type="status" data-color="purple">Governance layer</span></td></tr>
</tbody>
</table>
<div data-type="panel-warning"><p>Honest caveat: advanced / AI / FinOps / anomaly surfaces are partially implemented and only become useful once query-log telemetry ingestion is switched on. Until a fleet opts into that, expect empty charts on those pages — not broken ones. This is a rollout gap, not a code gap.</p></div>
<h2>Research grounding</h2>
<p>Cryo's features aren't invented from scratch. Several map directly to recent papers, with a working test exercising the core logic of each.</p>
<table>
<thead><tr><th>Paper</th><th>Feature</th></tr></thead>
<tbody>
<tr><td><strong>AutoComp</strong> (SIGMOD 2025, LinkedIn — <a href="http://www.cs.umd.edu/~abadi/papers/autocomp-sigmod.pdf">paper</a>)</td><td>Multi-objective compaction at 100K+ tables: rank by benefit vs compute cost; top-K under budget — same problem shape as Cryo's Governor ROI gate</td></tr>
<tr><td><strong>Proof-Carrying AI Agents</strong></td><td>Plan Verifier — statically checks a plan against the live catalog before enqueue</td></tr>
<tr><td><strong>EasyTUS</strong></td><td>Embedding-based unionable-table search across the fleet</td></tr>
<tr><td><strong>Observation Lakehouse</strong> (SANER 2026)</td><td>Append-only archive of every actuation, for audit and replay</td></tr>
<tr><td><strong>Zero-Scan Data Quality</strong> (FORMATS '26)</td><td>Per-column null rates, value bounds, and drift from manifest metadata — no data scans required</td></tr>
<tr><td><strong>Variance-aware QPP</strong> (CLOUD 2026)</td><td>Query performance prediction — the Governor refuses jobs predicted to blow p95</td></tr>
<tr><td><strong>PTO</strong> (IBM Research, SIGMOD/PACMMOD 2026)</td><td>Joint partition/sort/file-size optimization — 11–36% avg latency reduction reported on TPC workloads</td></tr>
<tr><td><strong>WAIR</strong> (arXiv 2602.23289, 2026)</td><td>Workload-aware incremental reclustering — closest match to Smart Maintenance layout scoring</td></tr>
</tbody>
</table>
<div data-type="panel-info"><p>PTO's full text sits behind the ACM Digital Library paywall — we verified abstract and reported numbers, not algorithm internals.</p></div>
<h2>Benefits</h2>
<table>
<thead><tr><th>Dimension</th><th>Before</th><th>With Cryo</th></tr></thead>
<tbody>
<tr><td>Maintenance trigger</td><td>Someone remembers, or cron fires blindly</td><td>Continuous health scoring decides what's worth doing</td></tr>
<tr><td>Cost awareness</td><td>Every rewrite runs regardless of payoff</td><td>ROI gate refuses rewrites that cost more than they save</td></tr>
<tr><td>Cross-engine dispatch</td><td>Hand-wired per table, per script</td><td>Governor routes to native fast-path or Spark/Trino</td></tr>
<tr><td>Blast radius</td><td>One bad table can stall a batch</td><td>Per-table locks and failure isolation</td></tr>
<tr><td>Visibility</td><td>Tribal knowledge and one-off scripts</td><td>Fleet dashboards, audit timeline, live run console</td></tr>
<tr><td>Test confidence</td><td>—</td><td>379 backend + 58 frontend tests on launch-tier surface</td></tr>
</tbody>
</table>
<p><em>The honest version: Cryo doesn't make Iceberg maintenance disappear. It makes it something a health score decides instead of something a person has to remember.</em></p>
<h2>Cost projections</h2>
<p>Cryo's Scanner already computes a real per-table S3 cost estimate as part of every health grade — this isn't a section where we invent a company-wide savings number. It's a walkthrough of the <em>shape</em> of that model, so the estimate a table actually gets isn't a black box.</p>
<table>
<thead><tr><th>Cost driver</th><th>What changes</th><th>Illustrative example</th></tr></thead>
<tbody>
<tr><td>Storage reclaimed</td><td><code>expire_snapshots</code> + orphan cleanup</td><td>~400 GB unreferenced snapshots freed on one neglected table</td></tr>
<tr><td>Request cost</td><td>Fewer, larger files per scan</td><td>10,000 files → ~500 ≈ 95% fewer object reads per scan</td></tr>
<tr><td>Bytes scanned</td><td>Better pruning and sort order</td><td>WAIR/PTO report 11–36% avg latency reduction on benchmark workloads</td></tr>
<tr><td>Compaction compute</td><td>ROI gate skips negative-payoff rewrites</td><td>The gate <em>is</em> the cost control</td></tr>
</tbody>
</table>
"""
)

BODY_PARTS.append(embed_image(
    "chart-small-files-bar.png",
    "10,000 small files compacted to ~480 on one table — the request-cost shape maintenance is trying to fix.",
))

BODY_PARTS.append(
    mermaid(
        """
xychart-beta
    title "Illustrative: small-file compaction, one table"
    x-axis [Before, After]
    y-axis "File count" 0 --> 12000
    bar [10000, 480]
"""
    )
)

BODY_PARTS.append(
    """
<div data-type="panel-warning"><p>These numbers are illustrative shapes, not a projection for any specific fleet. Real figures come from pointing the Scanner at your own tables — that's the point of the cost estimate being a built-in feature rather than a spreadsheet someone maintains separately.</p></div>
<h2>Future scope</h2>
<table>
<thead><tr><th>Horizon</th><th>Direction</th><th>Why it's next</th></tr></thead>
<tbody>
<tr><td>Near-term</td><td>Multi-tenant isolation on by default</td><td>Needed before shared infra across teams</td></tr>
<tr><td>Near-term</td><td>AI/FinOps/anomaly dashboards → launch-tier</td><td>Gated on telemetry adoption, not missing code</td></tr>
<tr><td>Mid-term</td><td>Broaden native fast-path coverage</td><td>Fewer Spark/Trino fallbacks</td></tr>
<tr><td>Mid-term</td><td>Flink production job submission</td><td>Today: config generation only</td></tr>
<tr><td>Long-term</td><td>Confidence-gated autonomous layout</td><td>Skip approval only for repeat-confirmed recommendations</td></tr>
<tr><td>Long-term</td><td>Cross-catalog federation</td><td>Single pane across REST, Hive, Glue</td></tr>
</tbody>
</table>
<h2>OSS today, and a real SaaS option tomorrow</h2>
<p>Cryo is Apache-2.0 licensed today — genuinely open, not source-available-with-an-asterisk. Any team can read the planner, audit the Governor's admission logic, and self-host without a vendor relationship.</p>
<ul>
<li><p><strong>OSS core stays the core</strong>: scanner, planner, governor, engines, presets, and the UI.</p></li>
<li><p><strong>Managed control plane</strong> could remove painful self-host ops (Postgres, Redis, K8s) for teams that want the dashboard without operating the stack.</p></li>
<li><p><strong>Enterprise governance</strong> — SSO, audit retention, multi-tenant isolation by default — is the gap the honest "who this isn't for yet" list already names.</p></li>
</ul>
<div data-type="panel-info"><p>Strategic option, not roadmap commitment. Multi-tenant SaaS isn't supported today by design — tenant isolation is opt-in, not default.</p></div>
<h2>Trade-offs — what's still open</h2>
<div data-type="panel-warning"><p>Sort/z-order execution is deliberately not autonomous: a recommendation sits in <code>pending</code> until a human approves it. A physical rewrite of a production table is resource-consuming and semi-reversible — we'd rather have a human in the loop than a system quietly reshuffling 13,236 tables on its own judgment.</p></div>
<p>The Spark/Trino fallback path is slower and costlier than the native fast path by design. The ROI gate still runs in shadow mode for some destinations while we build confidence in its cost model. Launch-tier surface (scanner, health grading, planner, governor, engines, presets, audit log, auth) is real and tested; advanced AI/FinOps/anomaly tiers are honestly labeled partial until telemetry adoption catches up.</p>
<h2>Further reading — where we grounded this page</h2>
<p>Curated links we used while writing (and for onboarding engineers who want the concepts behind Cryo's health deductions). Prefer the <a href="https://iceberg.apache.org/docs/latest/maintenance/">official maintenance docs</a> for procedure semantics; use vendor posts for operational war stories.</p>
<table>
<thead><tr><th>Topic</th><th>Link</th><th>Why it matters here</th></tr></thead>
<tbody>
<tr><td>Iceberg fundamentals</td><td><a href="https://iceberg.apache.org/">iceberg.apache.org</a></td><td>Hidden partitioning, time travel, multi-engine — what the format promises</td></tr>
<tr><td>How Iceberg works</td><td><a href="https://medium.com/@tfmv/how-apache-iceberg-actually-works-64f97fb13c45">How Iceberg actually works (TFMV)</a></td><td>Snapshots and metadata — good mental model for the Scanner</td></tr>
<tr><td>Small files</td><td><a href="https://www.dremio.com/blog/apache-iceberg-small-files-problem-causes-fixes-and-prevention/">Dremio small-files guide</a></td><td>Detection, OPTIMIZE, prevention — matches our small-file health deductions</td></tr>
<tr><td>Merge-on-read</td><td><a href="https://iceberglakehouse.com/iceberg/iceberg-merge-on-read/">MoR overview</a> · <a href="https://iceberglakehouse.com/iceberg/iceberg-positional-deletes/">positional deletes</a></td><td>Why 1,344 delete files on one scan is plausible</td></tr>
<tr><td>Delete overhead</td><td><a href="https://lakeops.dev/blog/iceberg-delete-files-merge-on-read">LakeOps delete-file overhead</a></td><td>Read-time cost compounds across every downstream query</td></tr>
<tr><td>Snapshot + orphan GC</td><td><a href="https://risingwave.com/blog/iceberg-snapshot-expiration-gc/">RisingWave expiration vs GC</a></td><td>Why expire and orphan are separate steps</td></tr>
<tr><td>CoW vs MoR</td><td><a href="https://www.cloudera.com/blog/technical/optimization-strategies-for-iceberg-tables.html">Cloudera optimization strategies</a></td><td>When MoR wins on writes and loses on reads without compaction</td></tr>
<tr><td>Maintenance landscape</td><td><a href="https://overcast.blog/9-apache-iceberg-table-maintenance-tools-you-should-know-df864ed7a6d5">9 maintenance tools (Overcast)</a> · <a href="https://lakeops.dev/blog/iceberg-compaction-tools-compared">LakeOps tool comparison</a></td><td>Where Cryo sits vs DIY, Glue, Trino-only schedulers</td></tr>
<tr><td>AutoComp research</td><td><a href="https://doi.org/10.48550/arxiv.2504.04186">arXiv AutoComp</a></td><td>Fleet-scale ROI compaction — Governor's conceptual cousin</td></tr>
</tbody>
</table>
<h2>Where this leaves us</h2>
<p>None of this makes Iceberg maintenance solved — it makes it observable, prioritized, and mostly automatic, which is a very different thing from either "someone remembers to run a script" or "a vendor's black box decides for us." We've already caught real bugs this way: a <code>retain_last</code> setting silently protecting every snapshot on a low-snapshot-count table, a hardcoded catalog name breaking cross-catalog dispatch, a connection pool sized for a world without 1,344-delete-file tables.</p>
<p><em>The wound that opened this page is closed. The next one is probably already growing on some table we haven't scanned yet — which is exactly why Cryo runs continuously instead of when someone remembers to check.</em></p>
<p>Questions, pushback, or a table you think is quietly rotting? Come find us.</p>
"""
)

if __name__ == "__main__":
    print("".join(BODY_PARTS))
