import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CryoTooltipProvider } from "../../components/ui/tooltip";
import { S3RequestsTab } from "./S3RequestsTab";

vi.mock("../../lib/auth", () => ({
  useAuth: () => ({
    api: { token: "test-token" },
    token: "test-token",
    role: "operator",
    subject: "tester",
    loading: false,
    error: null,
    isAuthenticated: true,
    signIn: vi.fn(),
    signOut: vi.fn()
  })
}));

vi.mock("../../api/client", () => ({
  listSources: vi.fn(),
  listSourceS3LogConfigs: vi.fn().mockResolvedValue([]),
  getSourceS3RequestCostRollup: vi.fn()
}));

import { getSourceS3RequestCostRollup, listSources } from "../../api/client";

describe("S3RequestsTab", () => {
  it("does not present a partial request-cost sum as the fleet total", async () => {
    vi.mocked(listSources).mockResolvedValue([
      { id: "s1", name: "lake", type: "glue" },
      { id: "s2", name: "raw", type: "hive" }
    ] as never);
    vi.mocked(getSourceS3RequestCostRollup).mockImplementation(async (_api, sourceId) => {
      if (sourceId === "s1") {
        return {
          total_request_count_30d: 12,
          total_cost_usd_month: 4,
          by_bucket: [],
          approximate: false
        } as never;
      }
      throw new Error("unavailable");
    });

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <CryoTooltipProvider>
          <S3RequestsTab />
        </CryoTooltipProvider>
      </QueryClientProvider>
    );

    expect(await screen.findAllByText(/1 of 2 measured/)).toHaveLength(2);
    const cost = screen.getByText("Estimated monthly request cost").closest(".cryo-metric-tile");
    expect(cost).toHaveTextContent(/1 of 2 measured/);
    expect(cost).not.toHaveTextContent("$0");
    const requests = screen.getByText("Requests (30d, fleet)").closest(".cryo-metric-tile");
    expect(requests).toHaveTextContent(/1 of 2 measured/);
  });
});
