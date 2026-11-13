import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { filtersFromSearchParams, filtersToSearchParamEntries } from "../../../lib/tableFilters";
import { tableFiltersToPredicate } from "../../../lib/predicate";
import { buildFilterChips } from "../../../lib/filterChips";
import { LifecycleBadge } from "../../LifecycleBadge";
import { TooltipProvider } from "../../ui/tooltip";
import { TableLifecycleCard } from "./TableLifecycleCard";

const { getTable, putTableLifecycle } = vi.hoisted(() => ({
  getTable: vi.fn(),
  putTableLifecycle: vi.fn()
}));

vi.mock("../../../api/client", () => ({ getTable, putTableLifecycle }));
vi.mock("../../../lib/auth", () => ({
  useAuth: () => ({ api: { token: "t" }, token: "t", role: "admin", subject: "someone" })
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const baseTable = {
  id: "table-1",
  source_id: "source-1",
  schema_name: "backups",
  table_name: "orders_backup",
  first_seen_at: "2026-01-01T00:00:00Z",
  is_active: true
};

const renderCard = (allowed = true) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <TooltipProvider>
        <TableLifecycleCard tableId="table-1" access={{ allowed, hidden: false, reason: allowed ? null : "Admin only" }} />
      </TooltipProvider>
    </QueryClientProvider>
  );

describe("TableLifecycleCard", () => {
  beforeEach(() => {
    getTable.mockReset().mockResolvedValue({ ...baseTable, lifecycle: "active", archive_candidate: true });
    putTableLifecycle.mockReset().mockResolvedValue({ message: "Marked as archived." });
  });

  it("suggests archiving a stale table and needs a reason and the typed name", async () => {
    renderCard();
    expect(await screen.findByTestId("archive-candidate")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Mark as archived" }));
    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "Mark as archived" });
    expect(confirm).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText("Reason"), { target: { value: "Nightly backup copy" } });
    fireEvent.change(within(dialog).getByLabelText("Type orders_backup to confirm"), {
      target: { value: "orders_backup" }
    });
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);
    await waitFor(() =>
      expect(putTableLifecycle).toHaveBeenCalledWith({ token: "t" }, "table-1", {
        lifecycle: "archived",
        reason: "Nightly backup copy",
        confirm: "orders_backup"
      })
    );
  });

  it("offers Return to active on an archived table", async () => {
    getTable.mockResolvedValue({ ...baseTable, lifecycle: "archived", lifecycle_reason: "backup" });
    renderCard();
    expect(await screen.findByRole("button", { name: "Return to active" })).toBeInTheDocument();
    expect(screen.getByTestId("lifecycle-badge")).toHaveTextContent("Archived");
  });

  it("disables the action without the admin role", async () => {
    renderCard(false);
    expect(await screen.findByRole("button", { name: "Mark as archived" })).toBeDisabled();
  });
});

describe("LifecycleBadge", () => {
  it("renders only for archived tables", () => {
    const { rerender } = render(
      <TooltipProvider>
        <LifecycleBadge lifecycle="active" />
      </TooltipProvider>
    );
    expect(screen.queryByTestId("lifecycle-badge")).not.toBeInTheDocument();
    rerender(
      <TooltipProvider>
        <LifecycleBadge lifecycle="archived" />
      </TooltipProvider>
    );
    expect(screen.getByTestId("lifecycle-badge")).toBeInTheDocument();
  });
});

describe("lifecycle filter", () => {
  it("round-trips through the URL and becomes a filter-spec leaf", () => {
    const filters = filtersFromSearchParams(new URLSearchParams("lifecycle=archived"));
    expect(filters.lifecycle).toBe("archived");
    expect(filtersToSearchParamEntries(filters)).toContainEqual(["lifecycle", "archived"]);
    expect(JSON.stringify(tableFiltersToPredicate(filters))).toContain('{"field":"lifecycle","op":"=","value":"archived"}');
    expect(filtersFromSearchParams(new URLSearchParams("lifecycle=bogus")).lifecycle).toBeNull();
  });

  it("shows a removable chip", () => {
    const onPatch = vi.fn();
    const chips = buildFilterChips({ lifecycle: "archived" }, [], onPatch);
    const chip = chips.find((c) => c.key === "lifecycle");
    expect(chip?.label).toBe("Archived only");
    chip?.onRemove();
    expect(onPatch).toHaveBeenCalledWith({ lifecycle: null });
  });
});
