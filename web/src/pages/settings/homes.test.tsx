import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { Suspense } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { SettingsAccessHome, SettingsAdminHome } from "./homes";

vi.mock("../../lib/auth", () => ({
  useAuth: () => ({ role: "viewer", subject: "viewer", api: { token: "t" }, token: "t" })
}));
vi.mock("./AccessControl", () => ({ AccessControl: () => null }));
vi.mock("../governance/PoliciesTab", () => ({ PoliciesTab: () => null }));

const renderAt = (path: string) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
    <MemoryRouter initialEntries={[path]}>
      <Suspense fallback={null}>
      <Routes>
        <Route path="/settings/admin" element={<SettingsAdminHome />} />
        <Route path="/settings/access" element={<SettingsAccessHome />} />
        <Route path="/settings/connections" element={<p>Connections page</p>} />
      </Routes>
      </Suspense>
    </MemoryRouter>
    </QueryClientProvider>
  );

describe("Settings homes (#498)", () => {
  it("tells a viewer the admin page needs the admin role instead of redirecting", () => {
    renderAt("/settings/admin");
    expect(screen.getByRole("heading", { name: "Admin" })).toBeInTheDocument();
    expect(screen.getByText("Admin role required")).toBeInTheDocument();
    expect(screen.queryByText("Connections page")).not.toBeInTheDocument();
  });

  it("gives Access & policy its own heading", async () => {
    renderAt("/settings/access");
    expect(await screen.findByRole("heading", { name: "Access & policy" })).toBeInTheDocument();
  });
});
