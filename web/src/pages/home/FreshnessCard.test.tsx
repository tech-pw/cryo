import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { FreshnessCard, type FreshnessRow } from "./FreshnessCard";

const rows: FreshnessRow[] = [
  {
    key: "within_24h",
    label: "Updated in last 24 hours",
    count: 10,
    percentage: 50,
    href: "/catalogs?freshness=within_24h"
  },
  {
    key: "failed",
    label: "Failed / corrupt tables",
    count: 2,
    percentage: 10,
    href: "/catalogs?freshness=failed"
  },
  {
    key: "unknown",
    label: "No snapshot timestamp",
    count: 0,
    percentage: 0,
    href: "/catalogs?freshness=unknown"
  }
];

const renderCard = (props: Partial<{ rows: FreshnessRow[]; asOf: string | null; loading: boolean }> = {}) =>
  render(
    <MemoryRouter>
      <FreshnessCard rows={rows} asOf="2026-08-04T12:00:00Z" loading={false} {...props} />
    </MemoryRouter>
  );

const barOf = (name: RegExp): HTMLElement => {
  const link = screen.getByRole("link", { name });
  const bar = link.querySelector("[data-freshness-bar]");
  expect(bar).not.toBeNull();
  return bar as HTMLElement;
};

describe("FreshnessCard", () => {
  it("paints a failed bucket with the danger token", () => {
    renderCard();
    const bar = barOf(/Failed \/ corrupt tables: .*Open matching tables/);
    const marker = `${bar.getAttribute("style") ?? ""} ${bar.className}`;
    expect(marker).toMatch(/status-danger|var\(--color-status-danger\)/);
    expect(marker).not.toMatch(/status-warning|grade-f|grade-c/);
    expect(bar.getAttribute("style") ?? "").toContain("min-width: 2px");
  });

  it("renders a zero-count row", () => {
    renderCard();
    const link = screen.getByRole("link", { name: /No snapshot timestamp: 0 tables/ });
    expect(link).toBeInTheDocument();
    const bar = link.querySelector("[data-freshness-bar]");
    expect(bar?.getAttribute("style") ?? "").toContain("width: 0%");
    expect(bar?.getAttribute("style") ?? "").not.toContain("min-width: 2px");
  });

  it("keeps the share table inside collapsed details", () => {
    renderCard();
    const summary = screen.getByText("Counts and share");
    expect(summary.tagName).toBe("SUMMARY");
    const details = summary.closest("details");
    expect(details).not.toBeNull();
    expect(details).not.toHaveAttribute("open");
    const table = details?.querySelector("table");
    expect(table).not.toBeNull();
    expect(table?.querySelector("caption")).toHaveClass("sr-only");
    expect(table).not.toBeVisible();
    expect(table?.textContent).toContain("50.0%");
  });

  it("uses chart primary for age buckets and faint for unknown", () => {
    renderCard();
    const fresh = barOf(/Updated in last 24 hours: .*Open matching tables/);
    const unknown = barOf(/No snapshot timestamp: 0 tables/);
    expect(fresh.getAttribute("style")).toContain("var(--color-chart-primary)");
    expect(unknown.getAttribute("style")).toContain("var(--color-ink-faint)");
  });

  it("titles the zone and hides as-of when there are no rows", () => {
    renderCard({ rows: [], asOf: "2026-08-04T12:00:00Z" });
    const heading = screen.getByRole("heading", { level: 2, name: "Table freshness and staleness" });
    expect(heading).toHaveClass("text-[18px]", "leading-[26px]", "font-semibold");
    expect(heading).not.toHaveClass("text-sm");
    expect(screen.getByText("No snapshot ages yet.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Run a catalog scan" })).toHaveAttribute("href", "/runs/scanner");
    expect(screen.queryByText(/As of/)).not.toBeInTheDocument();
    expect(screen.queryByText("Counts and share")).not.toBeInTheDocument();
    expect(heading.closest(".mb-0")).not.toBeNull();
    expect(heading.closest(".mb-0")?.className).not.toContain("mb-4");
  });
});
