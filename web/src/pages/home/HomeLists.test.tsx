import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { DashboardImprovedTable, TableRead } from "../../api/types";
import { HotTablesCard } from "./HotTablesCard";
import { ImprovedCard } from "./ImprovedCard";

const hot = (id: string, name: string, grade: string, queries: number) =>
  ({ id, schema_name: "analytics", table_name: name, stats: { grade, query_count_30d: queries, small_files_count: 900, total_size_gb: 12 } }) as unknown as TableRead;

const improved: DashboardImprovedTable = {
  table_id: "t9", schema_name: "sales", table_name: "orders", previous_grade: "D", previous_score: 52,
  grade: "B", overall_score: 76, improved_at: new Date(Date.now() - 18 * 60_000).toISOString()
};

describe("Home table lists", () => {
  it("ranks hot D/F tables and links each one to its detail page", () => {
    render(<MemoryRouter><HotTablesCard tables={[hot("t1", "events", "F", 18420), hot("t2", "views", "D", 9310)]} total={4} loading={false} error={false} /></MemoryRouter>);
    const rows = within(screen.getByRole("list", { name: "Queried tables at grade D or F" })).getAllByRole("link");
    expect(rows[0]).toHaveAccessibleName("analytics.events, grade F, 18,420 queries in 30 days");
    expect(rows[0]).toHaveAttribute("href", "/tables/t1");
    expect(screen.getByText("Showing 2 of 4.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /All D and F tables/ })).toHaveAttribute("href", "/tables?grade=D,F");
  });
  it("explains an empty hot list instead of implying a healthy lake", () => {
    render(<MemoryRouter><HotTablesCard tables={[]} total={0} loading={false} error={false} /></MemoryRouter>);
    expect(screen.getByText("No queried tables at D or F.")).toBeInTheDocument();
  });
  it("shows the grade change and score gained for tables Cryo fixed", () => {
    render(<MemoryRouter><ImprovedCard tables={[improved]} total={3} loading={false} error={false} /></MemoryRouter>);
    const row = screen.getByRole("link", { name: "sales.orders improved from grade D to B" });
    expect(row).toHaveAttribute("href", "/tables/t9");
    expect(row).toHaveTextContent("+24 pts");
    expect(row).toHaveTextContent("18m ago");
    expect(screen.getByText("3 tables improved in total.")).toBeInTheDocument();
  });
  it("keeps an improved-list error distinct from no improvements", () => {
    const { rerender } = render(<MemoryRouter><ImprovedCard tables={null} total={null} loading={false} error /></MemoryRouter>);
    expect(screen.getByText("Unavailable")).toBeInTheDocument();
    rerender(<MemoryRouter><ImprovedCard tables={[]} total={0} loading={false} error={false} /></MemoryRouter>);
    expect(screen.getByText("No grade improvements yet today.")).toBeInTheDocument();
  });
});
