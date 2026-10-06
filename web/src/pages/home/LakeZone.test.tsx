import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { gradeBreakdown, LakeZone, type LakeZoneProps } from "./LakeZone";

const base: LakeZoneProps = {
  tracked: 100, distribution: [{ grade: "A", count: 80 }, { grade: "D", count: 12 }, { grade: "F", count: 3 }],
  loading: false, error: false, sizeGb: 12.5, smallFiles: 40
};
const renderZone = (overrides: Partial<LakeZoneProps> = {}) => render(<MemoryRouter><LakeZone {...base} {...overrides} /></MemoryRouter>);
describe("Lake health overview", () => {
  it("provides exact counts and keyboard-operable grade filters beside the chart", () => {
    renderZone();
    expect(screen.getByRole("link", { name: /tables at D or F/ })).toHaveAttribute("href", "/tables?grade=D,F");
    const critical = screen.getByRole("link", { name: "Grade F, Critical: 3 tables" });
    expect(critical).toHaveAttribute("href", "/tables?grade=F");
    critical.focus();
    expect(critical).toHaveFocus();
    expect(screen.getByText("Not graded").closest("li")).toHaveTextContent("5");
    expect(screen.getByRole("list", { name: "Grade distribution" })).toHaveTextContent("80");
  });
  it("does not double-count explicit ungraded buckets or show impossible proportions", () => {
    expect(gradeBreakdown([{ grade: "A", count: 80 }, { grade: "Not graded", count: 20 }], 100)?.ungraded).toBe(20);
    expect(gradeBreakdown([{ grade: "A", count: 101 }], 100)).toBeNull();
    expect(gradeBreakdown([{ grade: "F", count: NaN }], 100)).toBeNull();
    expect(gradeBreakdown([{ grade: "F", count: -1 }], 100)).toBeNull();
  });
  it("keeps missing inventory distinct from zero", () => {
    renderZone({ sizeGb: null });
    expect(screen.getByText("Not measured")).toBeInTheDocument();
    expect(screen.queryByText("0 GB")).not.toBeInTheDocument();
  });
  it("distinguishes unknown data, zero tables, and completely ungraded tables", () => {
    const { rerender } = renderZone({ distribution: null });
    expect(screen.getByText("Grade split unknown")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /tables at D or F/ })).not.toBeInTheDocument();
    rerender(<MemoryRouter><LakeZone {...base} distribution={[]} tracked={0} /></MemoryRouter>);
    expect(screen.getByText("No tracked tables")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Connect a catalog" })).toHaveAttribute("href", "/settings/connections");
    rerender(<MemoryRouter><LakeZone {...base} distribution={[]} /></MemoryRouter>);
    expect(screen.getByText("Ungraded tables still need a scan.")).toBeInTheDocument();
    expect(screen.queryByText("No tables in the two lowest grades.")).not.toBeInTheDocument();
  });
  it("keeps errors and loading in the layout without drawing a healthy chart", () => {
    const { rerender } = renderZone({ error: true });
    expect(screen.getByText(/Table health is unavailable/)).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Grade distribution" })).not.toBeInTheDocument();
    rerender(<MemoryRouter><LakeZone {...base} loading /></MemoryRouter>);
    expect(screen.getByRole("region", { name: "Lake health" })).toHaveAttribute("aria-busy", "true");
  });
});
