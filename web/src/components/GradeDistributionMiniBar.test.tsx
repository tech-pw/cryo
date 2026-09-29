import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { GradeDistributionMiniBar } from "./GradeDistributionMiniBar";

const renderBar = (distribution: Array<{ grade: string; count: number }>, tracked?: number | null) =>
  render(
    <MemoryRouter>
      <GradeDistributionMiniBar distribution={distribution} tracked={tracked} />
    </MemoryRouter>
  );

describe("GradeDistributionMiniBar", () => {
  it("shows all six grades including zeroes and the graded denominator", () => {
    renderBar(
      [
        { grade: "A", count: 2 },
        { grade: "D", count: 1 }
      ],
      5
    );

    expect(screen.getByText("graded 3 of 5")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "F 0" })).toHaveAttribute("href", "/tables?grade=F");
    expect(screen.getByRole("link", { name: "D 1" })).toHaveAttribute("href", "/tables?grade=D");
    expect(screen.getByRole("link", { name: "C 0" })).toHaveAttribute("href", "/tables?grade=C");
    expect(screen.getByRole("link", { name: "B 0" })).toHaveAttribute("href", "/tables?grade=B");
    expect(screen.getByRole("link", { name: "A 2" })).toHaveAttribute("href", "/tables?grade=A");
    expect(screen.getByRole("link", { name: "Not graded 2" })).toHaveAttribute("href", "/health");
  });

  it("does not invent a Not graded count when the tracked total is missing", () => {
    renderBar([{ grade: "A", count: 2 }]);

    expect(screen.getByText("graded 2 of unknown")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Not graded Unknown" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Not graded 0" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "F 0" })).toBeInTheDocument();
  });
});
