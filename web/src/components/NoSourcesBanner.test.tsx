import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { listSources } from "../api/client";
import { NoSourcesBanner } from "./NoSourcesBanner";

vi.mock("../api/client", () => ({ listSources: vi.fn() }));
vi.mock("../lib/auth", () => ({
  useAuth: () => ({ api: { token: "t" }, token: "t" })
}));

const renderBanner = (client: QueryClient) =>
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <NoSourcesBanner />
      </MemoryRouter>
    </QueryClientProvider>
  );

describe("NoSourcesBanner", () => {
  beforeEach(() => vi.mocked(listSources).mockReset());

  it("shows on a genuinely empty install", async () => {
    vi.mocked(listSources).mockResolvedValue([]);
    renderBanner(new QueryClient({ defaultOptions: { queries: { retry: false } } }));

    expect(await screen.findByText("No sources connected yet")).toBeInTheDocument();
  });

  it("stays hidden once a source exists", async () => {
    vi.mocked(listSources).mockResolvedValue([{ id: "s1", name: "cdp" } as never]);
    renderBanner(new QueryClient({ defaultOptions: { queries: { retry: false } } }));

    await waitFor(() => expect(listSources).toHaveBeenCalled());
    expect(screen.queryByText("No sources connected yet")).not.toBeInTheDocument();
  });

  it("clears when ['sources'] is invalidated after connecting a catalog", async () => {
    // The regression this guards: the banner used a private query key, so the
    // invalidation fired on save never reached it and staleTime kept serving the
    // cached "zero sources" answer -- telling operators to connect a catalog they
    // had just connected.
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    vi.mocked(listSources).mockResolvedValue([]);
    renderBanner(client);
    expect(await screen.findByText("No sources connected yet")).toBeInTheDocument();

    vi.mocked(listSources).mockResolvedValue([{ id: "s1", name: "cdp" } as never]);
    await client.invalidateQueries({ queryKey: ["sources"] });

    await waitFor(() =>
      expect(screen.queryByText("No sources connected yet")).not.toBeInTheDocument()
    );
  });
});
