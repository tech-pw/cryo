import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { PageBreadcrumbs } from "./PageBreadcrumbs";

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
  listPresets: vi.fn().mockResolvedValue([])
}));

const renderAt = (path: string) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } }
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="*" element={<PageBreadcrumbs />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe("PageBreadcrumbs", () => {
  it("renders nothing on the root", () => {
    renderAt("/");
    expect(screen.queryByText("Tables")).toBeNull();
  });

  it("labels nested table-detail routes with the route map", () => {
    renderAt("/tables/abc-123");
    const nav = document.querySelector('[aria-label="Breadcrumb"]') as HTMLElement;
    expect(nav.textContent).toContain("Tables");
    expect(nav.textContent).toContain("abc-123");
  });
});
