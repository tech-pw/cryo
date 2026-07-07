import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { QueryState } from "./QueryState";

describe("QueryState", () => {
  it("renders skeleton when loading", () => {
    render(
      <QueryState isLoading>
        {() => <div>data</div>}
      </QueryState>
    );
    expect(screen.getByRole("status", { name: /loading/i })).toBeInTheDocument();
  });

  it("renders error alert on error", () => {
    render(
      <QueryState isError error={new Error("kaboom")}>
        {() => <div>data</div>}
      </QueryState>
    );
    expect(screen.getByRole("alert")).toHaveTextContent("kaboom");
  });

  it("renders empty alert when isEmpty", () => {
    render(
      <QueryState isEmpty emptyTitle="No tables" emptyDescription="Add a source to begin">
        {() => <div>data</div>}
      </QueryState>
    );
    expect(screen.getByText("No tables")).toBeInTheDocument();
    expect(screen.getByText("Add a source to begin")).toBeInTheDocument();
  });

  it("renders children when data is present", () => {
    render(
      <QueryState data={{ id: 1 }}>
        {(d) => <div>id={d.id}</div>}
      </QueryState>
    );
    expect(screen.getByText("id=1")).toBeInTheDocument();
  });
});