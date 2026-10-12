import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { FreshnessCard, type FreshnessRow } from "./FreshnessCard";

const rows: FreshnessRow[] = [
  { key: "within_24h", label: "Updated in last 24 hours", count: 10, percentage: 50, href: "/tables?freshness=within_24h" },
  { key: "failed", label: "Failed / corrupt tables", count: 2, percentage: 10, href: "/tables?freshness=failed" },
  { key: "unknown", label: "No snapshot timestamp", count: 0, percentage: 0, href: "/tables?freshness=unknown" }
];
const renderCard = (override: Partial<{ rows: FreshnessRow[]; asOf: string | null; loading: boolean }> = {}) =>
  render(<MemoryRouter><FreshnessCard rows={rows} asOf="2026-09-28T12:00:00Z" loading={false} {...override} /></MemoryRouter>);
describe("Snapshot age distribution", () => {
  it("uses fleet share for bar length and exposes exact values without a hover", () => {
    renderCard();
    const link = screen.getByRole("link", { name: /Updated in last 24 hours: 10 tables. 50.0%/ });
    expect(link.querySelector("[data-freshness-bar]")).toHaveStyle({ width: "50%" });
    expect(link).toHaveTextContent("50.0%");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    link.focus();
    expect(link).toHaveFocus();
  });
  it("distinguishes failed metadata from snapshot age using text and color", () => {
    renderCard();
    const failed = screen.getByRole("link", { name: /Failed \/ corrupt tables/ });
    expect(failed.querySelector("[data-freshness-bar]")?.getAttribute("style")).toContain("--color-status-danger");
    expect(failed).toHaveAttribute("href", "/tables?freshness=failed");
    expect(screen.getByText(/Older does not always mean unhealthy/)).toBeInTheDocument();
  });
  it("does not invent a visible minimum bar for zero tables", () => {
    renderCard();
    const link = screen.getByRole("link", { name: /No snapshot timestamp: 0 tables/ });
    expect(link.querySelector("[data-freshness-bar]")).toHaveStyle({ width: "0%" });
  });
  it("guides first-time users to a scan without presenting a stale timestamp", () => {
    renderCard({ rows: [] });
    expect(screen.getByRole("heading", { name: "Table freshness" })).toBeInTheDocument();
    expect(screen.getByText("No snapshot ages yet.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Run a catalog scan" })).toHaveAttribute("href", "/runs/scanner?tab=config");
    expect(screen.queryByText(/As of/)).not.toBeInTheDocument();
  });
});
