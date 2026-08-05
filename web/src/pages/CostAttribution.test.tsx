import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { buildCostConcentrationRows, CostAttribution } from "./CostAttribution";

vi.mock("../lib/auth", () => ({
  useAuth: () => ({
    api: { token: "test-token" },
    token: "test-token",
    role: "admin",
    subject: "tester",
    loading: false,
    error: null,
    isAuthenticated: true,
    signIn: vi.fn(),
    signOut: vi.fn()
  })
}));

vi.mock("../api/client", () => ({
  getCostAttributionDashboard: vi.fn()
}));

import { getCostAttributionDashboard } from "../api/client";
import type { CostAttributionResponse } from "../api/client";

const EMPTY_RESPONSE: CostAttributionResponse = {
  attributions: [],
  total_cost_usd: 0,
  unattributed_cost_usd: 0,
  month: "2026-07"
};

const POPULATED_RESPONSE: CostAttributionResponse = {
  attributions: [
    {
      cost_center: "platform",
      department: null,
      storage_cost_usd: 100,
      compute_cost_usd: 20,
      maintenance_cost_usd: 5,
      total_cost_usd: 125,
      table_count: 4,
      month: "2026-07"
    },
    {
      cost_center: "growth",
      department: null,
      storage_cost_usd: 40,
      compute_cost_usd: 10,
      maintenance_cost_usd: 0,
      total_cost_usd: 50,
      table_count: 2,
      month: "2026-07"
    }
  ],
  total_cost_usd: 175,
  unattributed_cost_usd: 0,
  month: "2026-07"
};

const renderPage = (initialEntry = "/cost-attribution") => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/cost-attribution" element={<CostAttribution />} />
          <Route path="/cost" element={<div>Cost page</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe("buildCostConcentrationRows", () => {
  it("sorts cost centers by total cost", () => {
    expect(
      buildCostConcentrationRows([
        { name: "Platform", value: 20 },
        { name: "Growth", value: 80 },
        { name: "Finance", value: 40 }
      ])
    ).toEqual([
      { name: "Growth", value: 80 },
      { name: "Finance", value: 40 },
      { name: "Platform", value: 20 }
    ]);
  });

  it("aggregates the long tail without losing its total", () => {
    const result = buildCostConcentrationRows(
      [
        { name: "A", value: 70 },
        { name: "B", value: 60 },
        { name: "C", value: 50 },
        { name: "D", value: 40 }
      ],
      2
    );

    expect(result).toEqual([
      { name: "A", value: 70 },
      { name: "B", value: 60 },
      { name: "Other (2)", value: 90 }
    ]);
    expect(result.reduce((total, item) => total + item.value, 0)).toBe(220);
  });
});

describe("CostAttribution page", () => {
  it("labels the page as experimental and explains why real data isn't available when empty", async () => {
    vi.mocked(getCostAttributionDashboard).mockResolvedValue(EMPTY_RESPONSE);
    renderPage();

    expect(await screen.findByText("Experimental")).toBeInTheDocument();
    expect(
      await screen.findByText("Cost attribution is an experimental, not-yet-implemented view")
    ).toBeInTheDocument();
    // Must not imply that a config step (e.g. "Open connections") will make this work today.
    expect(screen.queryByRole("link", { name: "Open connections" })).not.toBeInTheDocument();
  });

  it("keeps the selected month in the URL and re-queries when it changes", async () => {
    vi.mocked(getCostAttributionDashboard).mockResolvedValue(EMPTY_RESPONSE);
    renderPage();

    const select = await screen.findByLabelText("Month", { selector: "select" });
    expect(await screen.findByText("Experimental")).toBeInTheDocument();

    fireEvent.change(select, { target: { value: "2026-05" } });

    await waitFor(() => expect(getCostAttributionDashboard).toHaveBeenCalledWith(expect.anything(), "2026-05"));
  });

  it("restores the selected month from a ?month= URL param on load", async () => {
    vi.mocked(getCostAttributionDashboard).mockResolvedValue(EMPTY_RESPONSE);
    renderPage("/cost-attribution?month=2026-04");

    await waitFor(() =>
      expect(getCostAttributionDashboard).toHaveBeenCalledWith(expect.anything(), "2026-04")
    );
  });

  it("renders a semantic totals footer and disables the honest not-available view-tables action", async () => {
    vi.mocked(getCostAttributionDashboard).mockResolvedValue(POPULATED_RESPONSE);
    renderPage();

    const table = await screen.findByRole("table");
    expect(await within(table).findByText("platform")).toBeInTheDocument();

    const footer = table.querySelector("tfoot");
    expect(footer).not.toBeNull();
    expect(within(footer as HTMLElement).getByText("TOTAL")).toBeInTheDocument();
    expect(within(footer as HTMLElement).getByText("$175")).toBeInTheDocument();
    expect(within(footer as HTMLElement).getByText("100%")).toBeInTheDocument();

    const viewButtons = screen.getAllByRole("button", { name: /View tables/ });
    viewButtons.forEach((button) => expect(button).toBeDisabled());
  });
});

