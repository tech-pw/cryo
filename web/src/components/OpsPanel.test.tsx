import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { getDeepHealth } from "../api/client";
import { OpsPanel } from "./OpsPanel";

vi.mock("../api/client", () => ({
  getDeepHealth: vi.fn().mockResolvedValue({ status: "ok", checks: { db: { status: "ok" } } }),
  getShallowHealth: vi.fn().mockResolvedValue({ status: "ok" }),
  getMetricsText: vi.fn(),
  triggerGitOpsWebhook: vi.fn()
}));
vi.mock("../lib/auth", () => ({ useAuth: () => ({ api: { token: "t" }, token: "t" }) }));

describe("OpsPanel", () => {
  it("uses the authenticated API and links to neither /metrics nor /healthz/deep", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <OpsPanel />
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(await screen.findByText("db")).toBeInTheDocument();
    expect(getDeepHealth).toHaveBeenCalledWith({ token: "t" });
    const hrefs = screen.queryAllByRole("link").map((a) => a.getAttribute("href"));
    expect(hrefs).not.toContain("/metrics");
    expect(hrefs).not.toContain("/healthz/deep");
  });
});
