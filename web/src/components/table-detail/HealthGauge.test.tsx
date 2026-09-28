import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HealthGauge } from "./HealthGauge";

describe("HealthGauge", () => {
  it("renders a letter grade and an integer score without the arc or days-to-critical", () => {
    render(<HealthGauge score={82.4} grade="B" daysToCritical={3} trendLabel="falling" />);

    expect(screen.getByRole("group", { name: "Grade B, score 82" })).toBeInTheDocument();
    expect(screen.getByText("B")).toBeInTheDocument();
    expect(screen.getByText("82")).toBeInTheDocument();
    expect(screen.queryByText(/Days to Critical/i)).not.toBeInTheDocument();
    expect(document.querySelector("svg")).toBeNull();
  });

  it("shows Not scanned when there is no grade", () => {
    const { rerender } = render(<HealthGauge score={80} grade={null} daysToCritical={2} />);
    expect(screen.getByRole("status")).toHaveTextContent("Not scanned");
    expect(screen.queryByText("80")).not.toBeInTheDocument();
    expect(screen.queryByText(/Days to Critical/i)).not.toBeInTheDocument();

    rerender(<HealthGauge score={null} />);
    expect(screen.getByRole("status")).toHaveTextContent("Not scanned");
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });
});
