import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { SearchResults } from "./SearchResults";
import type { SearchResponse } from "../api/client";

vi.mock("../lib/auth", () => ({
  useAuth: () => ({
    api: { token: "test-token" },
    token: "test-token"
  })
}));

const searchCatalog = vi.fn();

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    searchCatalog: (...args: Parameters<typeof actual.searchCatalog>) => searchCatalog(...args)
  };
});

const buildResponse = (overrides: Partial<SearchResponse> = {}): SearchResponse => ({
  query: "orders",
  total: 2,
  groups: [
    {
      type: "tables",
      label: "Tables",
      total: 2,
      items: [
        {
          id: "t1",
          type: "tables",
          name: "orders",
          qualified_name: "prod.sales.orders",
          catalog: "prod",
          schema_name: "sales",
          table_id: "t1",
          match_reason: "name"
        },
        {
          id: "t2",
          type: "tables",
          name: "orders_archive",
          qualified_name: "prod.sales.orders_archive",
          catalog: "prod",
          schema_name: "sales",
          table_id: "t2",
          match_reason: "name"
        }
      ]
    }
  ],
  ...overrides
});

const renderAt = (path: string) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <SearchResults />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

const SameRouteNavigationHarness = () => {
  const navigate = useNavigate();
  return (
    <>
      <button type="button" onClick={() => navigate("/search?q=customers")}>
        Search customers from topbar
      </button>
      <SearchResults />
    </>
  );
};

describe("SearchResults page", () => {
  beforeEach(() => {
    searchCatalog.mockReset();
  });

  it("prompts for a query and does not call the API when q is empty", () => {
    renderAt("/search");

    expect(screen.getByText(/Enter a query above/i)).toBeInTheDocument();
    expect(searchCatalog).not.toHaveBeenCalled();
  });

  it("links overflow results to the tables search route, not a dropped kind param", async () => {
    searchCatalog.mockResolvedValue(
      buildResponse({
        total: 3,
        groups: [
          {
            type: "tables",
            label: "Tables",
            total: 3,
            items: [
              {
                id: "t1",
                type: "tables",
                name: "orders",
                qualified_name: "prod.sales.orders",
                catalog: "prod",
                schema_name: "sales",
                table_id: "t1",
                match_reason: "name"
              }
            ]
          }
        ]
      })
    );
    renderAt("/tables?q=orders&notice=search");

    const seeAll = await screen.findByRole("link", { name: /view all tables/i });
    expect(seeAll).toHaveAttribute("href", "/tables?q=orders&notice=search&group=tables");
  });

  it("shows results, group counts, and a polite result-count announcement", async () => {
    searchCatalog.mockResolvedValue(buildResponse());
    renderAt("/search?q=orders");

    expect(await screen.findByRole("link", { name: "prod.sales.orders" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent('2 results for "orders".');
    expect(screen.getByRole("button", { name: "All · 2" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Tables · 2" })).toBeInTheDocument();
    expect(searchCatalog).toHaveBeenCalledWith(
      { token: "test-token" },
      expect.objectContaining({ q: "orders", group: undefined, offset: undefined })
    );
  });

  it("shows a friendly error with retry and technical details on failure", async () => {
    searchCatalog.mockRejectedValue(new Error("Failed to fetch"));
    renderAt("/search?q=orders");

    expect(await screen.findByText("Search is unavailable right now.")).toBeInTheDocument();
    expect(screen.getByText("Technical details")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent('Search failed for "orders".');

    searchCatalog.mockResolvedValueOnce(buildResponse());
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByRole("link", { name: "prod.sales.orders" })).toBeInTheDocument();
  });

  it("shows an empty state when there are no matches", async () => {
    searchCatalog.mockResolvedValue(buildResponse({ total: 0, groups: [] }));
    renderAt("/search?q=zzz");

    expect(await screen.findByText("No matches")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent('No results for "zzz".');
  });

  it("switches the group filter and requeries scoped to that group", async () => {
    searchCatalog.mockResolvedValue(buildResponse());
    renderAt("/search?q=orders");

    await screen.findByRole("link", { name: "prod.sales.orders" });
    fireEvent.click(screen.getByRole("button", { name: "Tables · 2" }));

    await waitFor(() =>
      expect(searchCatalog).toHaveBeenLastCalledWith(
        { token: "test-token" },
        expect.objectContaining({ q: "orders", group: "tables", offset: 0 })
      )
    );
  });

  it("submits the visible search field and updates the query", async () => {
    searchCatalog.mockResolvedValue(buildResponse());
    renderAt("/search?q=orders");

    await screen.findByRole("link", { name: "prod.sales.orders" });
    fireEvent.change(screen.getByLabelText("Search query"), { target: { value: "customers" } });
    fireEvent.click(screen.getByRole("button", { name: /^Search$/ }));

    await waitFor(() =>
      expect(searchCatalog).toHaveBeenLastCalledWith(
        { token: "test-token" },
        expect.objectContaining({ q: "customers" })
      )
    );
  });

  it("keeps the visible field in sync with same-route URL navigation", async () => {
    searchCatalog.mockResolvedValue(buildResponse());
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/search?q=orders"]}>
          <SameRouteNavigationHarness />
        </MemoryRouter>
      </QueryClientProvider>
    );

    await screen.findByRole("link", { name: "prod.sales.orders" });
    fireEvent.click(screen.getByRole("button", { name: "Search customers from topbar" }));

    await waitFor(() => expect(screen.getByLabelText("Search query")).toHaveValue("customers"));
    expect(searchCatalog).toHaveBeenLastCalledWith(
      { token: "test-token" },
      expect.objectContaining({ q: "customers" })
    );
  });

  it("paginates within a selected group using the offset API", async () => {
    searchCatalog.mockResolvedValue(
      buildResponse({
        total: 120,
        groups: [
          {
            type: "tables",
            label: "Tables",
            total: 120,
            items: Array.from({ length: 50 }, (_, index) => ({
              id: `t${index}`,
              type: "tables" as const,
              name: `table_${index}`,
              qualified_name: `prod.sales.table_${index}`,
              catalog: "prod",
              schema_name: "sales",
              table_id: `t${index}`,
              match_reason: "name" as const
            }))
          }
        ]
      })
    );
    renderAt("/search?q=orders&group=tables");

    await screen.findByRole("link", { name: "prod.sales.table_0" });
    expect(screen.getByText("Showing 1–50 of 120")).toBeInTheDocument();

    const nextButton = screen.getByRole("button", { name: "Next page" });
    fireEvent.click(nextButton);

    await waitFor(() =>
      expect(searchCatalog).toHaveBeenLastCalledWith(
        { token: "test-token" },
        expect.objectContaining({ q: "orders", group: "tables", offset: 50 })
      )
    );
  });
});
