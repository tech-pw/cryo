import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { LineageGraph } from "./LineageGraph";

describe("LineageGraph", () => {
  it("lists upstream and downstream names with depth and edge type", () => {
    render(
      <MemoryRouter>
        <LineageGraph
          data={{
            root_table_id: "root",
            root_table_name: "events",
            upstream: [{ table_id: "u1", table_name: "raw_events", depth: 2, node_type: "read" }],
            downstream: [{ table_id: "d1", table_name: "daily_events", depth: 1, node_type: "write" }]
          }}
        />
      </MemoryRouter>
    );

    expect(screen.getByRole("heading", { name: "Upstream" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Downstream" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "raw_events" })).toHaveTextContent("raw_events");
    expect(screen.getByRole("link", { name: "daily_events" })).toBeInTheDocument();
    expect(screen.getAllByText(/depth 2/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/depth 1/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/read/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/write/).length).toBeGreaterThan(0);
  });
});
