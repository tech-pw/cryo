# Confluence visuals — Cryo maintenance crew page

Assets for [Cryo: Why Apache Iceberg Needs a Maintenance Crew](https://physicswallah001.atlassian.net/wiki/spaces/DEA/pages/2304180246).

## Attach images (one-time, ~2 minutes)

The page body references these files by name. Confluence MCP cannot upload attachments; bind them manually:

1. Open the page in **edit** mode.
2. Drag all files from this folder into the editor (names must match exactly).
3. Publish — Confluence replaces each `PASTE IMAGE · filename` blockquote with the attachment.

### Illustrations (AI-generated)

| File | Section |
|------|---------|
| `cryo-hero-iceberg-rot.png` | Hook — Day 0 vs Day 90 rot |
| `cryo-four-rots-infographic.png` | Four decay modes |
| `cryo-architecture-flow.png` | Cryo HLD |
| `cryo-library-maintenance-crew.png` | Library metaphor |
| `cryo-health-grades-dashboard.png` | Fleet health UI |
| `cryo-compaction-bar-chart.png` | Optional duplicate of matplotlib chart |

### Charts (matplotlib)

| File | Section |
|------|---------|
| `chart-fleet-scale.png` | 13,236 tables scale |
| `chart-health-deductions.png` | Health score deductions |
| `chart-small-files-bar.png` | Before/after file count |

## Regenerate page HTML

```bash
python3 docs/assets/confluence/cryo-maintenance-crew/build_page_html.py > docs/assets/confluence/cryo-maintenance-crew/page-body.html
```

Publish via Atlassian MCP `updateConfluencePage` with `contentFormat: html`.

## v5 content additions (reference-grounded)

- Maintenance playbook table + ordered pipeline diagram (expire → orphan → compact → manifests)
- Compaction strategies table (binpack / sort / z-order)
- Inline links to official Iceberg docs, Dremio, LakeOps, RisingWave, etc.
- Further reading table at end for onboarding / video explainer source material
