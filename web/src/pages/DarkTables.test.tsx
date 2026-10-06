import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { DarkTables } from "./DarkTables";

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
  getDarkTables: vi.fn(),
  dismissDarkTable: vi.fn()
}));

import { dismissDarkTable, getDarkTables } from "../api/client";
import type { DarkTableCandidatesResponse } from "../api/types";

const EMPTY: DarkTableCandidatesResponse = {
  candidates: [],
  total_candidates: 0,
  total_size_gb: 0,
  total_estimated_monthly_cost_usd: 0
};

const POPULATED: DarkTableCandidatesResponse = {
  candidates: [
    {
      id: "c1",
      table_id: "t1",
      schema_name: "sample",
      table_name: "bronze_offers",
      consecutive_zero_query_windows: 5,
      first_zero_query_at: "2026-08-10T00:00:00Z",
      last_checked_at: "2026-08-14T00:00:00Z",
      total_size_gb: 5205.1,
      estimated_monthly_cost_usd: 129.77,
      status: "candidate"
    }
  ],
  total_candidates: 1,
  total_size_gb: 5205.1,
  total_estimated_monthly_cost_usd: 129.77
};

const renderPage = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/dark-tables"]}>
        <Routes>
          <Route path="/dark-tables" element={<DarkTables />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe("DarkTables page", () => {
  it("renders an empty state when there are no candidates", async () => {
    vi.mocked(getDarkTables).mockResolvedValue(EMPTY);

    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/No confirmed dark tables/i)).toBeInTheDocument();
    });
  });

  it("renders a candidate with its size, cost, and confirmation window count", async () => {
    vi.mocked(getDarkTables).mockResolvedValue(POPULATED);

    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/sample\.bronze_offers/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/5 consecutive daily checks/i)).toBeInTheDocument();
  });

  it("submits a dismiss with the operator's reason and refreshes the list", async () => {
    vi.mocked(getDarkTables).mockResolvedValue(POPULATED);
    vi.mocked(dismissDarkTable).mockResolvedValue({
      table_id: "t1",
      status: "dismissed",
      dismissed_by: "tester",
      dismissed_at: "2026-08-14T12:00:00Z",
      dismissed_reason: "quarterly report table"
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/sample\.bronze_offers/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /not dark — dismiss/i }));
    const input = screen.getByPlaceholderText(/why is this table fine to keep/i);
    fireEvent.change(input, { target: { value: "quarterly report table" } });
    fireEvent.click(screen.getByRole("button", { name: /confirm/i }));

    await waitFor(() => {
      expect(dismissDarkTable).toHaveBeenCalledWith(
        { token: "test-token" },
        "t1",
        { reason: "quarterly report table" }
      );
    });
  });

  it("disables Confirm until a reason is entered", async () => {
    vi.mocked(getDarkTables).mockResolvedValue(POPULATED);

    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/sample\.bronze_offers/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /not dark — dismiss/i }));
    expect(screen.getByRole("button", { name: /confirm/i })).toBeDisabled();
  });
});
