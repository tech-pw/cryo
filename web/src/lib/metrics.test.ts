import { createElement } from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Stat } from "../components/ui/stat";
import {
  METRIC_IDS,
  METRIC_PROVENANCE,
  METRICS,
  metric,
  metricLabel,
  metricTooltip,
  provenanceLabel,
  type MetricId
} from "./metrics";

const REQUIRED_IDS: MetricId[] = [
  "tracked_tables",
  "scanned_tables",
  "graded_tables",
  "tables_grade_d_or_f",
  "needs_compaction",
  "storage_gb",
  "modeled_s3_storage_cost",
  "modeled_savings",
  "measured_reclaim_gb",
  "awaiting_person",
  "failed_runs_24h",
  "running_runs"
];

describe("metric dictionary", () => {
  it("gives every id a definition, unit, and provenance", () => {
    expect(METRICS.map((entry) => entry.id)).toEqual([...METRIC_IDS]);
    for (const id of METRIC_IDS) {
      const entry = metric(id);
      expect(entry.definition.trim().length).toBeGreaterThan(0);
      expect(entry.unit.trim().length).toBeGreaterThan(0);
      expect(entry.denominator.trim().length).toBeGreaterThan(0);
      expect(entry.label.trim().length).toBeGreaterThan(0);
      expect(entry.source).toMatch(/\/api\/v1\//);
      expect(METRIC_PROVENANCE).toContain(entry.provenance);
      expect(metricLabel(id)).toBe(entry.label);
      const tooltip = metricTooltip(id);
      expect(tooltip).toContain(entry.definition);
      expect(tooltip).toContain(entry.unit);
      expect(tooltip).toContain(provenanceLabel(entry.provenance));
      expect(tooltip).toContain(entry.denominator);
      expect(metric(id)).toBe(entry);
    }
  });

  it("includes the starting headline ids", () => {
    for (const id of REQUIRED_IDS) {
      expect(METRIC_IDS).toContain(id);
    }
  });

  it("keeps Health and catalogs storage cost as different ids", () => {
    const health = metric("modeled_s3_storage_cost");
    const catalogsCosts = METRICS.filter(
      (entry) => entry.id.includes("catalog") && entry.id.includes("cost") && entry.id !== health.id
    );
    expect(catalogsCosts.length).toBeGreaterThan(0);
    for (const catalogs of catalogsCosts) {
      expect(catalogs.id).not.toBe(health.id);
      expect(catalogs.definition).not.toBe(health.definition);
      expect(catalogs.label).not.toBe(health.label);
    }
    expect(health.definition).toMatch(/not a bill/i);
    expect(health.definition).toMatch(/TableCost\.current_monthly_cost_usd/);
    expect(health.definition).toMatch(/TableStats\.estimated_monthly_s3_cost_usd/);
    expect(health.definition).toMatch(/active tables/i);
    expect(health.definition).toMatch(/not \$0/);
    const catalogs = metric("catalogs_modeled_s3_storage_cost");
    expect(catalogs.definition).toMatch(/not a bill/i);
    expect(catalogs.definition).toMatch(/enabled sources/);
    expect(catalogs.definition).toMatch(/TableStats\.estimated_monthly_s3_cost_usd/);
    expect(catalogs.definition).toMatch(/not \$0/);
    expect(catalogs.definition).not.toMatch(/TableCost\.current_monthly_cost_usd when a cost row exists, otherwise/);
    expect(health.provenance).toBe("modeled");
    expect(catalogs.provenance).toBe("modeled");
  });

  it("does not treat unknown reclaim as zero", () => {
    const reclaim = metric("measured_reclaim_gb");
    expect(reclaim.definition).toMatch(/not 0 GB/);
    expect(reclaim.definition).toMatch(/not bytes rewritten/);
    expect(reclaim.unit).toBe("GB");
  });
});

describe("Stat metricId", () => {
  it("shows the registry label and provenance chip", () => {
    render(createElement(Stat, { metricId: "tracked_tables", value: "12" }));
    expect(screen.getByText("Tracked tables")).toBeInTheDocument();
    expect(screen.getByText("Measured")).toBeInTheDocument();
    // The description lives in a Radix tooltip now, not a title attribute, so it is only
    // in the DOM once hovered. Assert the registry still supplies it rather than reaching
    // for markup that no longer exists -- getByTitle broke here when Stat moved to
    // TruncationTooltip and left main red.
    expect(metricTooltip("tracked_tables")).toMatch(/Every table row Cryo stores/);
  });

  it("keeps an explicit label when metricId is omitted", () => {
    render(createElement(Stat, { label: "Custom label", value: "1" }));
    expect(screen.getByText("Custom label")).toBeInTheDocument();
    expect(screen.queryByText("Measured")).not.toBeInTheDocument();
    expect(screen.queryByText("Modelled")).not.toBeInTheDocument();
  });

  it("uses the registry label when metricId is set", () => {
    render(createElement(Stat, { metricId: "modeled_s3_storage_cost", label: "S3 monthly now", value: "$1" }));
    expect(screen.getByText("Estimated storage cost")).toBeInTheDocument();
    expect(screen.queryByText("S3 monthly now")).not.toBeInTheDocument();
    expect(screen.getByText("Modelled")).toBeInTheDocument();
  });
});
