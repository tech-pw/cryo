import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RequestCostShare } from "./RequestCostShare";

describe("RequestCostShare", () => {
  it("names the comparison and does not paint a missing request cost as zero", () => {
    render(
      <RequestCostShare
        storageCostUsd={12}
        requestCostUsd={null}
        getRequests30d={null}
        putRequests30d={4}
        listRequests30d={null}
      />
    );

    expect(screen.getByText("estimated storage $ vs measured request $ (30 d)")).toBeInTheDocument();
    expect(screen.getByText("Not measured")).toBeInTheDocument();
    expect(screen.getByText(/Not measured GET, 4 PUT, Not measured LIST/)).toBeInTheDocument();
    expect(screen.queryByText(/0%/)).not.toBeInTheDocument();
  });
});
