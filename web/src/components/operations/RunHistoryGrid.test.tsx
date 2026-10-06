import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { RunRead } from "../../api/types";
import { MiniRunHistoryBar, RunHistoryGrid } from "./RunHistoryGrid";

const makeRun = (overrides: Partial<RunRead> = {}): RunRead => ({
  id: "11111111-2222-3333-4444-555555555555",
  kind: "scan",
  status: "succeeded",
  created_at: "2026-01-15T02:00:00Z",
  ...overrides
});

describe("MiniRunHistoryBar", () => {
  it("renders each run as an accessible link, not a role=img wrapper", () => {
    render(
      <MemoryRouter>
        <MiniRunHistoryBar
          runs={[makeRun({ id: "aaaaaaaa-0000-0000-0000-000000000001", status: "succeeded" }), makeRun({ id: "bbbbbbbb-0000-0000-0000-000000000002", status: "failed" })]}
          max={5}
        />
      </MemoryRouter>
    );

    const list = screen.getByRole("list", { name: /last 2 runs/i });
    expect(list).toBeInTheDocument();

    const links = screen.getAllByRole("link");
    expect(links).toHaveLength(2);
    expect(links[0]).toHaveAccessibleName(/aaaaaaaa.*succeeded/i);
    expect(links[1]).toHaveAccessibleName(/bbbbbbbb.*failed/i);
  });

  it("pads with hidden placeholders up to max without adding extra links", () => {
    render(
      <MemoryRouter>
        <MiniRunHistoryBar runs={[makeRun()]} max={4} />
      </MemoryRouter>
    );

    expect(screen.getAllByRole("link")).toHaveLength(1);
  });
});

describe("RunHistoryGrid", () => {
  it("shows the empty state text when there are no runs", () => {
    render(
      <MemoryRouter>
        <RunHistoryGrid runs={[]} emptyText="No runs yet." />
      </MemoryRouter>
    );
    expect(screen.getByText("No runs yet.")).toBeInTheDocument();
  });

  it("renders a link per run with its short id", () => {
    render(
      <MemoryRouter>
        <RunHistoryGrid runs={[makeRun()]} />
      </MemoryRouter>
    );
    expect(screen.getByText("11111111")).toBeInTheDocument();
  });
});
