# Product

## Register

product

## Users

Cryo is used by data platform engineers, SREs, operators, and occasional viewer stakeholders who manage Apache Iceberg table health across a lakehouse fleet. They work in focused operational sessions: scanning table condition, filtering for maintenance candidates, assigning presets, connecting catalogs and engines, previewing SQL/load impact, and monitoring scheduled or ad-hoc runs.

## Product Purpose

Cryo consolidates Iceberg maintenance and health into one self-hosted product. It replaces scattered Airflow DAGs, dbt macros, and reports with a UI and API for source/destination setup, table exploration, preset-driven maintenance, load-aware execution, health dashboards, run monitoring, and audit history.

Success means operators can safely understand the fleet, act on the right tables, and trust that maintenance respects partitioning, engine capacity, cooldowns, off-peak windows, and audit requirements.

## Brand Personality

Calm, exact, and operational. Cryo should feel like serious engineering infrastructure: quiet confidence, clear status, fast scanning, and no decorative noise. The product can borrow the polish and restraint of Linear, Stripe, and Vercel light-mode tools, but it must prioritize task clarity over brand theatrics.

## Anti-references

Do not make Cryo feel like consumer or marketing SaaS: no gradient hero cards, playful illustrations, oversized vanity metrics, gamified empty states, or decorative animations. Also avoid heavy legacy admin clutter: no dense chrome for its own sake, no noisy boxed panels, no wall of controls, and no ambiguous status badges.

## Design Principles

Show only useful information by default. Tables, dashboards, and details should expose the operational fields needed to decide what to do next, with progressive disclosure for diagnostics and raw SQL.

Make filtering the primary interaction. Users should intuitively narrow the fleet, select all matching tables across pages, assign presets, and run maintenance without learning an internal query language first.

Separate safety from decoration. Guardrails, previews, capacity, cooldowns, health grades, and audit trails must be visually legible and trustworthy; color and motion should clarify state, not add personality.

Keep sources and destinations mentally distinct. The UI should reinforce the product model: catalogs are read-side origins; engines are write-side execution targets chosen through presets and maintenance actions.

Prefer operator speed over spectacle. Navigation, tables, forms, and console views should be compact, predictable, keyboard-friendly, and resilient during high-pressure maintenance windows.

## Accessibility & Inclusion

Target WCAG 2.2 AA at minimum. Body text must meet at least 4.5:1 contrast, critical statuses should not rely on color alone, and grade/status indicators need labels or icons in addition to hue.

Support reduced motion, visible focus states, keyboard navigation for tables and filters, and dense layouts that remain readable under zoom. Use plain operator language for errors and confirmations so non-primary users can understand the risk before acting.
