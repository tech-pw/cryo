import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsLayout } from "./SettingsLayout";

let currentRole: "admin" | "viewer" = "viewer";

vi.mock("../../lib/auth", () => ({
  useAuth: () => ({
    api: { token: "test-token" },
    token: "test-token",
    role: currentRole,
    subject: "tester",
    loading: false,
    error: null,
    isAuthenticated: true,
    signIn: vi.fn(),
    signOut: vi.fn()
  })
}));

const renderLayout = (path = "/settings/connections") => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <SettingsLayout>
          <p>Section body</p>
        </SettingsLayout>
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe("SettingsLayout", () => {
  beforeEach(() => {
    currentRole = "viewer";
  });

  it("uses the settings subtitle and hides Admin from a viewer", () => {
    renderLayout();
    expect(screen.getByRole("heading", { name: "Settings" })).toBeInTheDocument();
    expect(screen.getByText("What is connected, and who may call the model?")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Admin" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Connections" })).toHaveAttribute("href", "/settings/connections");
    expect(screen.getByRole("link", { name: "Access & policy" })).toHaveAttribute("href", "/settings/access");
    expect(screen.getByText("Section body")).toBeInTheDocument();
  });

  it("shows Admin only for an admin", () => {
    currentRole = "admin";
    renderLayout("/settings/admin");
    expect(screen.getByRole("link", { name: "Admin" })).toHaveAttribute("href", "/settings/admin");
    expect(screen.getByRole("link", { name: "Admin" })).toHaveAttribute("aria-current", "page");
  });
});
