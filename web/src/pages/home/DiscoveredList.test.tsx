import { fireEvent, render, screen, within } from "@testing-library/react";
import { act } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DiscoveredList, type Discovery } from "./DiscoveredList";

const DESCRIPTION = "Fast-path flags for new or barely-scanned tables. These are not auto-compacted.";

const discovery = (
  tableId: string,
  severity: string,
  detectedAt: string,
  extra: Partial<Discovery> = {}
): Discovery => ({
  table_id: tableId,
  table_name: extra.table_name ?? `sales.${tableId}`,
  severity,
  finding_type: extra.finding_type ?? "small_files",
  reason: extra.reason ?? `${tableId} reason`,
  detected_at: detectedAt
});

const sevenFindings = (): Discovery[] => [
  discovery("info-old", "info", "2026-09-20T12:00:00Z"),
  discovery("info-mid", "info", "2026-09-27T12:00:00Z"),
  discovery("warn", "warning", "2026-09-29T11:00:00Z", { reason: "small files piled up" }),
  discovery("missing", "warning", "2026-09-29T09:00:00Z", {
    table_name: "sales.events_raw",
    finding_type: "missing_target_file_size",
    reason: "write.target-file-size-bytes is unset"
  }),
  discovery("info-new", "info", "2026-09-29T11:30:00Z", {
    finding_type: "partition_evolution_pending",
    reason: "barely scanned"
  }),
  discovery("crit", "critical", "2026-09-29T08:00:00Z", { reason: "older critical" }),
  discovery("err", "error", "2026-09-29T11:50:00Z", { reason: "too many small files" })
];

const renderList = ({
  findings,
  loading = false,
  error = null
}: {
  findings: Discovery[];
  loading?: boolean;
  error?: unknown;
}) =>
  render(
    <MemoryRouter>
      <DiscoveredList findings={findings} loading={loading} error={error} />
    </MemoryRouter>
  );

describe("DiscoveredList", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders five of seven rows, worst severity first, and a Show all button", () => {
    renderList({ findings: sevenFindings() });

    const list = screen.getByRole("list", { name: "Recently discovered" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows).toHaveLength(5);
    expect(screen.getByRole("heading", { name: /Recently discovered\s·\s7/ })).toHaveClass("text-sm", "font-semibold");
    expect(screen.getByRole("button", { name: "Show all" }).textContent).toBe("Show all");
    expect(screen.getByRole("button", { name: "Show all" }).querySelector("svg")).toBeNull();

    expect(within(rows[0]).getByRole("link", { name: "sales.err" })).toHaveAttribute("href", "/tables/err");
    expect(within(rows[0]).getByText("Critical")).toBeInTheDocument();
    expect(within(rows[1]).getByRole("link", { name: "sales.crit" })).toBeInTheDocument();
    expect(within(rows[2]).getByText("Warning")).toBeInTheDocument();
    expect(within(rows[4]).getByText("Info")).toBeInTheDocument();
    expect(within(list).queryByText("sales.info-mid")).not.toBeInTheDocument();

    expect(screen.getAllByText("Critical").length).toBeGreaterThan(0);
    expect(screen.queryByText("Partial")).not.toBeInTheDocument();
    expect(rows[0].querySelector("p")).toBeNull();
    expect(within(rows[0]).queryByRole("paragraph")).not.toBeInTheDocument();
    expect(within(rows[0]).queryByText("too many small files")).not.toBeInTheDocument();

    const name = within(rows[0]).getByRole("link", { name: "sales.err" });
    expect(name).toHaveClass("font-mono");
    expect(name.getAttribute("data-finding-tooltip")).toMatch(/^sales\.err\n/);
    expect(name.getAttribute("data-finding-tooltip")).toContain("too many small files");
    const describedBy = name.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    expect(document.getElementById(describedBy ?? "")).toHaveTextContent("too many small files");

    expect(rows[0].querySelector(".h-2.w-2")).toHaveAttribute("aria-hidden", "true");
    expect(rows[0].querySelector(".h-2.w-2")?.className).toContain("--color-status-danger");
    expect(rows[2].querySelector(".h-2.w-2")?.className).toContain("--color-status-warning");
    expect(rows[4].querySelector(".h-2.w-2")?.className).toContain("--color-muted-subtle");
    expect(within(rows[2]).getByText("Small files")).toBeInTheDocument();
    expect(within(rows[4]).getByText("partition evolution pending")).toBeInTheDocument();
    expect(screen.getByText(DESCRIPTION)).toBeInTheDocument();
    expect(document.querySelector("section")).toHaveClass("font-sans");
  });

  it("links a missing target size to property recommendations", () => {
    renderList({ findings: sevenFindings() });
    const list = screen.getByRole("list", { name: "Recently discovered" });
    const link = within(list).getByRole("link", { name: "Open property recommendations for sales.events_raw" });
    expect(link).toHaveAttribute("href", "/tables/missing?tab=files#files-properties");
    expect(link).toHaveTextContent("No target size");
    const name = within(list).getByRole("link", { name: "sales.events_raw" });
    expect(name.getAttribute("data-finding-tooltip")).toContain("Open property recommendations");
    expect(name.getAttribute("data-finding-tooltip")).toContain("write.target-file-size-bytes is unset");
  });

  it("formats age as now, minutes, hours, and days", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-29T12:00:00Z"));
    renderList({
      findings: [
        discovery("days", "info", "2026-09-26T12:00:00Z"),
        discovery("hours", "info", "2026-09-29T10:00:00Z"),
        discovery("minutes", "info", "2026-09-29T11:46:00Z"),
        discovery("just-now", "info", "2026-09-29T11:59:30Z")
      ]
    });

    const rows = screen.getAllByRole("listitem");
    expect(rows).toHaveLength(4);
    expect(rows[0].querySelector("time")).toHaveTextContent("now");
    expect(rows[1].querySelector("time")).toHaveTextContent("14m");
    expect(rows[2].querySelector("time")).toHaveTextContent("2h");
    expect(rows[3].querySelector("time")).toHaveTextContent("3d");
    expect(screen.queryByRole("button", { name: "Show all" })).not.toBeInTheDocument();
  });

  it("does not pad when fewer than five findings exist", () => {
    renderList({
      findings: [discovery("a", "info", "2026-09-29T11:00:00Z"), discovery("b", "warning", "2026-09-29T10:00:00Z")]
    });
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(within(screen.getAllByRole("listitem")[0]).getByText("Warning")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show all" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Recently discovered\s·\s2/ })).toBeInTheDocument();
  });

  it("shows the current empty copy without an illustration", () => {
    const { container } = renderList({ findings: [] });
    expect(screen.getByRole("heading", { name: /Recently discovered\s·\s0/ })).toBeInTheDocument();
    expect(screen.getByText("No recently discovered flags")).toBeInTheDocument();
    expect(
      screen.getByText("New tables that fail an early file-count or target-file-size check will show up here.")
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show all" })).not.toBeInTheDocument();
    expect(screen.queryByRole("listitem")).not.toBeInTheDocument();
    expect(container.querySelector("svg")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
  });

  it("shows five skeletons while loading and no count", () => {
    renderList({ findings: [], loading: true });
    const loading = screen.getByLabelText("Loading");
    expect(loading.querySelectorAll(".h-8")).toHaveLength(5);
    expect(screen.getByRole("heading", { name: "Recently discovered" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /·/ })).not.toBeInTheDocument();
    expect(screen.queryByText("No recently discovered flags")).not.toBeInTheDocument();
    expect(screen.queryByText("Still loading…")).not.toBeInTheDocument();
  });

  it("stops the shimmer after five seconds", () => {
    vi.useFakeTimers();
    renderList({ findings: [], loading: true });
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(screen.getByText("Still loading…")).toBeInTheDocument();
    expect(screen.getByLabelText("Loading").querySelector(".animate-pulse")).toBeNull();
  });

  it("shows the fast-path error and not the empty state", () => {
    renderList({ findings: [], error: new Error("catalog timed out") });
    expect(screen.getByRole("alert")).toHaveTextContent("Failed to load fast-path findings");
    expect(screen.getByText("catalog timed out")).toBeInTheDocument();
    expect(screen.queryByText("No recently discovered flags")).not.toBeInTheDocument();
    expect(screen.queryByRole("listitem")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Recently discovered" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show all" })).not.toBeInTheDocument();
  });

  it("opens every finding in a 512px sheet", async () => {
    renderList({ findings: sevenFindings() });
    fireEvent.click(screen.getByRole("button", { name: "Show all" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog.className).toContain("sm:max-w-lg");
    expect(dialog.className).not.toContain("sm:max-w-sm");
    expect(within(dialog).getByText(DESCRIPTION)).toBeInTheDocument();
    expect(within(dialog).getAllByRole("listitem")).toHaveLength(7);
    expect(within(dialog).getByText("sales.info-mid")).toBeInTheDocument();
    expect(within(dialog).getAllByText("Critical").length).toBeGreaterThan(0);
    expect(within(dialog).getAllByRole("listitem")[0].querySelector("p")).toBeNull();
  });
});
