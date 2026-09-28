import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { TableRunRead } from "../../../api/types";
import {
  auditRunJoinKey,
  groupAuditReceipts,
  HISTORY_PAGE_SIZE,
  historyPageFromSearch,
  MaintenanceHistory,
  type MaintenanceAuditRow
} from "./MaintenanceHistory";

const getTableAudit = vi.hoisted(() => vi.fn());
const getTableRuns = vi.hoisted(() => vi.fn());

vi.mock("../../../api/client", () => ({
  getTableAudit,
  getTableRuns
}));

vi.mock("../../../lib/auth", () => ({
  useAuth: () => ({ api: { token: "test-token" }, token: "test-token", role: "viewer" })
}));

const run = (overrides: Partial<TableRunRead> = {}): TableRunRead => ({
  job_id: "job-1",
  run_id: "run-1",
  status: "succeeded",
  mode: "execute",
  steps: [],
  started_at: "2026-09-28T01:00:00Z",
  ...overrides
});

const renderHistory = (search = "") => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/tables/table-1${search}`]}>
        <MaintenanceHistory tableId="table-1" />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe("audit → run join", () => {
  it("joins on job_id, then run_id, and never on timestamp", () => {
    expect(auditRunJoinKey({ job_id: "job-1", run_id: "run-9" })).toEqual({ kind: "job", id: "job-1" });
    expect(auditRunJoinKey({ run_id: "run-9" })).toEqual({ kind: "run", id: "run-9" });
    expect(auditRunJoinKey({ job_id: "  ", run_id: "" })).toBeNull();
    expect(auditRunJoinKey({})).toBeNull();

    const sameTime = "2026-09-28T01:00:00Z";
    const blocks = groupAuditReceipts(
      [{ action: "optimize", ts: sameTime } satisfies MaintenanceAuditRow],
      [run({ started_at: sameTime, finished_at: sameTime })]
    );
    expect(blocks).toEqual([{ kind: "ungrouped", row: expect.objectContaining({ action: "optimize" }) }]);
  });

  it("groups by job_id and sorts receipts in canonical order", () => {
    const blocks = groupAuditReceipts(
      [
        { id: 1, job_id: "job-1", action: "expire_snapshots", ts: "2026-09-28T03:00:00Z" },
        { id: 2, job_id: "job-1", action: "optimize", ts: "2026-09-28T02:00:00Z" },
        { id: 3, action: "remove_orphan_files", ts: "2026-09-28T02:00:00Z" }
      ],
      [run()]
    );
    expect(blocks[0]).toMatchObject({ kind: "run", join: { kind: "job", id: "job-1" }, run: { run_id: "run-1" } });
    if (blocks[0]?.kind !== "run") throw new Error("expected a run group");
    expect(blocks[0].receipts.map((row) => row.action)).toEqual(["optimize", "expire_snapshots"]);
    expect(blocks[1]).toMatchObject({ kind: "ungrouped" });
  });

  it("reads history_page as a positive integer", () => {
    expect(historyPageFromSearch(null)).toBe(1);
    expect(historyPageFromSearch("0")).toBe(1);
    expect(historyPageFromSearch("2")).toBe(2);
  });
});

describe("MaintenanceHistory", () => {
  it("lists an unkeyed receipt ungrouped and does not invent a run link", async () => {
    getTableAudit.mockResolvedValue([
      {
        id: 4,
        action: "optimize",
        ts: "2026-09-28T01:00:00Z",
        status: "succeeded",
        engine: null,
        duration_ms: null,
        actual_bytes_freed: null,
        input_files_count: null,
        output_files_count: null
      }
    ]);
    getTableRuns.mockResolvedValue([run({ started_at: "2026-09-28T01:00:00Z" })]);
    renderHistory();

    expect(await screen.findByText("Run link not recorded")).toBeInTheDocument();
    expect(screen.getByText("optimize")).toBeInTheDocument();
    expect(screen.getAllByText("not reported").length).toBeGreaterThan(0);
    expect(screen.queryByRole("link", { name: "Open run" })).not.toBeInTheDocument();
    expect(screen.queryByText("0 GB")).not.toBeInTheDocument();
    expect(document.querySelector("tr")).toBeNull();
  });

  it("links a job_id receipt to the run and discloses with a button", async () => {
    getTableAudit.mockResolvedValue([
      {
        id: 8,
        job_id: "job-1",
        action: "expire_snapshots",
        status: "failed",
        detail: "commit failed",
        engine: "trino",
        duration_ms: 1500,
        input_files_count: 4,
        output_files_count: 1,
        actual_bytes_freed: 1024
      },
      {
        id: 7,
        job_id: "job-1",
        action: "optimize",
        status: "succeeded",
        engine: "trino",
        duration_ms: 900
      }
    ]);
    getTableRuns.mockResolvedValue([run()]);
    renderHistory();

    const button = await screen.findByRole("button", { name: "Show receipts" });
    expect(button.tagName).toBe("BUTTON");
    expect(button.closest("tr")).toBeNull();
    expect(screen.getByRole("link", { name: "Open run" })).toHaveAttribute("href", "/runs/run-1");
    expect(screen.queryByText("optimize")).not.toBeInTheDocument();

    fireEvent.click(button);
    const optimize = screen.getByText("optimize");
    const expire = screen.getByText("expire_snapshots");
    expect(optimize.compareDocumentPosition(expire) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText("commit failed")).toBeInTheDocument();
    expect(screen.getByText("4 → 1")).toBeInTheDocument();
  });

  it("paginates with history_page", async () => {
    getTableAudit.mockResolvedValue(
      Array.from({ length: HISTORY_PAGE_SIZE }, (_, index) => ({
        id: index + 1,
        action: "compute_statistics",
        job_id: null
      }))
    );
    getTableRuns.mockResolvedValue([]);
    renderHistory("?tab=maintenance");

    const next = await screen.findByRole("link", { name: "Next page" });
    expect(next).toHaveAttribute("href", expect.stringContaining("history_page=2"));
    expect(next.getAttribute("href")).toContain("tab=maintenance");
    expect(getTableAudit).toHaveBeenCalledWith({ token: "test-token" }, "table-1", {
      limit: HISTORY_PAGE_SIZE,
      offset: 0
    });
  });
});
