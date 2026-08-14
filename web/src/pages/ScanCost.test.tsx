import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ScanCost } from "./ScanCost";

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
  getScanCostAttribution: vi.fn()
}));

import { getScanCostAttribution } from "../api/client";
import type { ScanCostAttributionResponse } from "../api/types";

const EMPTY: ScanCostAttributionResponse = {
  since: "2026-08-07",
  rows: [],
  by_client: [],
  total_query_count: 0,
  total_physical_gb_scanned: 0
};

const POPULATED: ScanCostAttributionResponse = {
  since: "2026-08-07",
  rows: [
    {
      client: "Metabase v0.60.0",
      schema_name: "pw_bq",
      query_count: 1835,
      failed_query_count: 12,
      physical_bytes_scanned: 1500 * 1024 ** 3,
      physical_gb_scanned: 1500
    },
    {
      client: "dbt-trino-1.9.2",
      schema_name: "offline",
      query_count: 500,
      failed_query_count: 0,
      physical_bytes_scanned: 50 * 1024 ** 3,
      physical_gb_scanned: 50
    }
  ],
  by_client: [
    {
      client: "Metabase v0.60.0",
      query_count: 1835,
      physical_bytes_scanned: 1500 * 1024 ** 3,
      physical_gb_scanned: 1500
    },
    {
      client: "dbt-trino-1.9.2",
      query_count: 500,
      physical_bytes_scanned: 50 * 1024 ** 3,
      physical_gb_scanned: 50
    }
  ],
  total_query_count: 2335,
  total_physical_gb_scanned: 1550
};

const renderPage = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/scan-cost"]}>
        <Routes>
          <Route path="/scan-cost" element={<ScanCost />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe("ScanCost page", () => {
  it("renders empty states when there is no scan data yet", async () => {
    vi.mocked(getScanCostAttribution).mockResolvedValue(EMPTY);

    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/No scan data yet/i)).toBeInTheDocument();
    });
  });

  it("ranks clients by bytes scanned, biggest first", async () => {
    vi.mocked(getScanCostAttribution).mockResolvedValue(POPULATED);

    renderPage();

    await waitFor(() => {
      expect(screen.getAllByText(/Metabase v0.60.0/i).length).toBeGreaterThan(0);
    });
    const clientNames = screen.getAllByTitle(/Metabase v0.60.0|dbt-trino-1.9.2/i);
    expect(clientNames[0]).toHaveTextContent("Metabase v0.60.0");
    expect(clientNames[1]).toHaveTextContent("dbt-trino-1.9.2");
  });

  it("shows failed query counts on the client+schema breakdown", async () => {
    vi.mocked(getScanCostAttribution).mockResolvedValue(POPULATED);

    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/12 failed/i)).toBeInTheDocument();
    });
  });

  it("requests a 7-day window by default", async () => {
    vi.mocked(getScanCostAttribution).mockResolvedValue(EMPTY);

    renderPage();

    await waitFor(() => {
      expect(getScanCostAttribution).toHaveBeenCalledWith({ token: "test-token" }, { days: 7 });
    });
  });
});
