import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { formatNumber, setDisplayTimeZone } from "../../../lib/format";
import { CryoTooltipProvider } from "../../ui/tooltip";
import { TableDetailHeader, type TableDetailHeaderProps } from "./TableDetailHeader";

const props = (overrides: Partial<TableDetailHeaderProps> = {}): TableDetailHeaderProps => ({
  tableId: "tbl-1",
  tableName: "events_daily",
  qualifiedName: "prod_lake.analytics.events_daily",
  sourceName: "prod_lake",
  catalogName: "prod_lake",
  schemaName: "analytics",
  scanned: true,
  score: 71,
  grade: "C",
  ownerEmail: "data-eng@example.com",
  partitionLabel: "day(event_ts)",
  location: "s3://bucket/events_daily",
  scanAt: "2026-09-28T03:40:00.000Z",
  lastCommitAt: "2026-09-28T03:25:00.000Z",
  sizeGb: 412,
  totalFiles: 38120,
  smallFiles: 21400,
  snapshotCount: 1204,
  lastMaintainedAt: "2026-09-26T20:44:00.000Z",
  lastMaintainedOperation: "optimize",
  workflowName: "nightly-standard",
  workflowSource: "preset",
  hasWorkflow: true,
  engineLabel: "Trino",
  engineSupported: true,
  engineReason: null,
  scanBackoff: null,
  writeAllowed: true,
  writeReason: null,
  scanAllowed: true,
  scanReason: null,
  onScan: vi.fn(),
  onRefreshStats: vi.fn(),
  onChooseWorkflow: vi.fn(),
  onPreview: vi.fn(),
  onRun: vi.fn(),
  operations: [
    { op: "optimize", label: "Compact files", destructive: false },
    { op: "remove_orphan_files", label: "Remove orphan files", destructive: true }
  ],
  onRunOperation: vi.fn(),
  operationsDisabledReason: null,
  onOpenSettings: vi.fn(),
  ...overrides
});

const renderHeader = (overrides: Partial<TableDetailHeaderProps> = {}) => {
  const value = props(overrides);
  render(
    <MemoryRouter>
      <TableDetailHeader {...value} />
    </MemoryRouter>
  );
  return value;
};

describe("TableDetailHeader", () => {
  afterEach(() => {
    setDisplayTimeZone(undefined);
  });
  it("shows Not scanned and Scan now when the table has not been scanned", () => {
    const onScan = vi.fn();
    renderHeader({
      scanned: false,
      score: null,
      grade: null,
      scanAt: null,
      sizeGb: null,
      totalFiles: null,
      smallFiles: null,
      snapshotCount: null,
      onScan
    });

    expect(screen.getByText("Not scanned")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Scan now" }));
    expect(onScan).toHaveBeenCalledOnce();
    expect(screen.queryByText("More table metrics")).not.toBeInTheDocument();
    expect(screen.queryByText("Needs compaction")).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("does not render an unknown score as 0", () => {
    renderHeader({
      scanned: false,
      score: null,
      grade: "C",
      sizeGb: null,
      totalFiles: null,
      smallFiles: null,
      snapshotCount: null
    });

    expect(screen.getByText("Not scanned")).toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
    expect(screen.queryByText("0 GB")).not.toBeInTheDocument();
    expect(screen.queryByText(/0 of 0/)).not.toBeInTheDocument();
    expect(screen.queryByText("C · 0")).not.toBeInTheDocument();
  });

  it("renders breadcrumb links as anchors", () => {
    renderHeader();
    const nav = screen.getByRole("navigation", { name: "Breadcrumb" });
    const links = within(nav).getAllByRole("link");

    expect(links).toHaveLength(4);
    for (const link of links) {
      expect(link.tagName).toBe("A");
      expect(link).toHaveAttribute("href");
    }
    expect(links[0]).toHaveAttribute("href", "/tables");
    expect(links[0]).toHaveTextContent("Tables");
    expect(links[1]).toHaveAttribute("href", "/tables?catalog=prod_lake");
    expect(links[1]).toHaveTextContent("prod_lake");
    expect(links[2]).toHaveAttribute("href", "/tables?catalog=prod_lake&schema=analytics");
    expect(links[2]).toHaveTextContent("analytics");
    expect(links[3]).toHaveAttribute("href", "/tables/tbl-1");
    expect(links[3]).toHaveTextContent("events_daily");
    expect(nav.querySelector("[onclick]")).toBeNull();
  });

  it("disables Run with the viewer write reason", () => {
    const reason = "Operators can run maintenance";
    const onRun = vi.fn();
    renderHeader({
      scanned: true,
      hasWorkflow: true,
      writeAllowed: false,
      writeReason: reason,
      onRun
    });

    fireEvent.click(screen.getByRole("button", { name: "More actions" }));
    const run = screen.getByRole("button", { name: "Run full workflow now" });
    expect(run).toBeDisabled();
    expect(run).toHaveAttribute("title", reason);
    expect(screen.getByText(reason)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Preview maintenance" })).toBeEnabled();
    fireEvent.click(run);
    expect(onRun).not.toHaveBeenCalled();
  });

  it("lists single operations, flags destructive ones, and disables them with a reason", () => {
    const onRunOperation = vi.fn();
    renderHeader({ scanned: true, hasWorkflow: true, onRunOperation });
    fireEvent.click(screen.getByRole("button", { name: "More actions" }));
    expect(screen.getByRole("group", { name: "Run one operation" })).toBeInTheDocument();
    expect(screen.getByText("deletes files")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Compact files" }));
    expect(onRunOperation).toHaveBeenCalledWith("optimize");
  });

  it("disables the full workflow run and single operations until a workflow is assigned", () => {
    renderHeader({
      scanned: true,
      hasWorkflow: false,
      workflowName: null,
      workflowSource: null,
      writeAllowed: true,
      operationsDisabledReason: "Assign a workflow first."
    });
    fireEvent.click(screen.getByRole("button", { name: "More actions" }));
    expect(screen.getByRole("button", { name: "Run full workflow now" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Compact files" })).toBeDisabled();
    expect(screen.getByText("Assign a workflow before running maintenance")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Choose a workflow" })).toBeEnabled();
  });

  it("labels scan and commit in the install's zone and does not show the raw timestamp", () => {
    setDisplayTimeZone("Asia/Kolkata");
    renderHeader();
    expect(screen.getByText("Scan 28 Sep 09:10 Kolkata")).toBeInTheDocument();
    expect(screen.getByText("Last commit 28 Sep 08:55 Kolkata")).toBeInTheDocument();
    expect(screen.queryByText(/2026-09-28T03:40/)).not.toBeInTheDocument();
    expect(screen.getByText("Latest action: Compaction · 27 Sep 02:14 Kolkata")).toBeInTheDocument();
  });

  it("labels scan and commit in UTC when the install is on UTC (#183)", () => {
    setDisplayTimeZone("UTC");
    renderHeader();
    // The same instant, in UTC: 03:40Z is 09:10 in Kolkata.
    expect(screen.getByText("Scan 28 Sep 03:40 UTC")).toBeInTheDocument();
  });

  it("does not invent a scan backoff date", () => {
    setDisplayTimeZone("Asia/Kolkata");
    const view = (backoff: TableDetailHeaderProps["scanBackoff"]) => (
      <CryoTooltipProvider>
        <MemoryRouter>
          <TableDetailHeader {...props({ scanBackoff: backoff })} />
        </MemoryRouter>
      </CryoTooltipProvider>
    );
    const { rerender } = render(view({ until: null, failures: 3 }));
    expect(screen.getByText("Scans paused after 3 failures")).toBeInTheDocument();
    expect(screen.queryByText(/until/i)).not.toBeInTheDocument();

    rerender(view({ until: "not-a-date", failures: 2 }));
    expect(screen.getByText("Scans paused after 2 failures")).toBeInTheDocument();
    expect(screen.queryByText("not-a-date")).not.toBeInTheDocument();

    rerender(view({ until: "2026-09-29T20:30:00.000Z", failures: 4 }));
    expect(screen.getByText("Scans paused until 30 Sep 02:00 Kolkata after 4 failures")).toBeInTheDocument();
    expect(screen.queryByText(/2026-09-29T20:30/)).not.toBeInTheDocument();
  });

  it("never shows the raw unpartitioned token", () => {
    renderHeader({ partitionLabel: "_unpartitioned" });
    expect(screen.queryByText("_unpartitioned")).not.toBeInTheDocument();
    expect(screen.getByText("Unpartitioned")).toBeInTheDocument();
    expect(screen.getByText("Iceberg")).toBeInTheDocument();
  });

  it("links the workflow to maintenance settings and lays out facts in a grid", () => {
    renderHeader();
    expect(screen.getByRole("link", { name: "nightly-standard (preset)" })).toHaveAttribute(
      "href",
      "/tables/tbl-1?tab=config#maintenance-settings"
    );
    expect(screen.getByText(formatNumber(21400))).toBeInTheDocument();
    expect(screen.getByText(`56% of ${formatNumber(38120)} files`)).toBeInTheDocument();
    const facts = screen.getByText("Table size").closest("dl");
    expect(facts?.className).toContain("ctd-header__facts");
    expect(screen.getByText("Runs on Trino")).toBeInTheDocument();
  });

  it("copies the current location href", () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    renderHeader();
    fireEvent.click(screen.getByRole("button", { name: "Copy link" }));
    expect(writeText).toHaveBeenCalledWith(window.location.href);
  });

  it("states when the engine is not supported without inventing a reason", () => {
    renderHeader({ engineSupported: false, engineLabel: "Spark", engineReason: "no compatible destination" });
    expect(screen.getByText("Maintenance unavailable: no compatible destination")).toBeInTheDocument();
    expect(screen.queryByText(/Runs on Spark/)).not.toBeInTheDocument();
  });
});
