import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { CryoTooltipProvider } from "../../components/ui/tooltip";
import { formatStorage, heroView, listingCoverage, storageSegments } from "../../lib/storageOverview";
import { StorageCard } from "./StorageCard";
import { storageFixture } from "./storageFixture";

const renderCard = (data = storageFixture()) =>
  render(<CryoTooltipProvider><MemoryRouter><StorageCard data={data} loading={false} error={false} /></MemoryRouter></CryoTooltipProvider>);

describe("StorageCard", () => {
  it("shows the measured hero, coverage and cumulative total", () => {
    renderCard();
    const hero = screen.getByRole("region", { name: "Last 24 hours" });
    expect(hero).toHaveTextContent("Cryo freed 1.5 TB");
    expect(hero).toHaveTextContent("212 tables cleaned");
    expect(hero).toHaveTextContent("≈ $35");
    expect(hero).toHaveTextContent("Measured on 75% of actions (300 of 400)");
    expect(hero).toHaveTextContent("41.0 TB freed since");
    expect(hero).toHaveTextContent("3 unmeasured days not counted");
  });

  it("breaks storage into labelled estimated segments with listing coverage", () => {
    renderCard();
    const breakdown = screen.getByRole("region", { name: "Where storage goes" });
    for (const label of ["Live data", "Snapshot history", "Dead", "Orphans", "Metadata"]) {
      expect(within(breakdown).getByText(label)).toBeInTheDocument();
    }
    expect(within(breakdown).getAllByText("freeable")).toHaveLength(2);
    expect(breakdown).toHaveTextContent("Storage listing for 812 of 1,044 tables");
    expect(breakdown).toHaveTextContent("20.0 TB is freeable");
    expect(breakdown).toHaveTextContent("Estimate");
  });

  it("teaches when actions ran without byte counts and when nothing ran", () => {
    const base = storageFixture();
    const { unmount } = renderCard(storageFixture({ freed_24h: { ...base.freed_24h, actions_measured: 0, bytes_freed: 0 } }));
    expect(screen.getByText(/Cryo cleaned/)).toHaveTextContent("Cryo cleaned 212 tables");
    expect(screen.getByText(/none reported a byte count/)).toBeInTheDocument();
    expect(screen.queryByText(/0 GB/)).not.toBeInTheDocument();
    unmount();
    renderCard(storageFixture({ freed_24h: { ...base.freed_24h, actions_succeeded: 0, actions_measured: 0, tables_cleaned: 0, bytes_freed: 0 } }));
    expect(screen.getByText("No cleanup ran in the last 24 hours")).toBeInTheDocument();
  });

  it("explains missing listing, missing trend and versioning", () => {
    const base = storageFixture();
    renderCard(storageFixture({
      breakdown: { ...base.breakdown, tables_with_listing: 0 },
      trend: [],
      cumulative: { bytes_freed: 0, since: null, days_recorded: 0, days_unmeasured: 0 },
      versioning_findings: [{ bucket: "bucket-a", tables: 12 }]
    }));
    expect(screen.getByText(/need an S3 listing/)).toBeInTheDocument();
    expect(screen.getByText("The trend is still recording")).toBeInTheDocument();
    expect(screen.getByText(/running total starts/)).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("1 bucket keeps deleted versions");
    expect(screen.getByRole("note")).toHaveTextContent("bucket-a");
  });
});

describe("storage helpers", () => {
  it("formats binary units and computes shares", () => {
    expect(formatStorage(1024 ** 4 * 24.7)).toBe("24.7 TB");
    expect(formatStorage(0)).toBe("0 GB");
    const segments = storageSegments(storageFixture().breakdown);
    expect(Math.round(segments.reduce((sum, s) => sum + s.pct, 0))).toBe(100);
    expect(listingCoverage(storageFixture().breakdown)).toBe("partial");
    expect(heroView({ ...storageFixture().freed_24h, actions_succeeded: 0 }).state).toBe("idle");
  });
});
