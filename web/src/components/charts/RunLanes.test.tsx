import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RunLanes } from "./RunLanes";

describe("RunLanes", () => {
  it("does not draw a bar when the start time was not recorded", () => {
    render(
      <RunLanes
        jobs={[
          {
            id: "1",
            table: "public.orders",
            engine: "spark",
            status: "succeeded",
            startedAt: null,
            finishedAt: null
          }
        ]}
      />
    );
    expect(screen.getAllByText("public.orders").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Not recorded").length).toBeGreaterThan(0);
  });
});
