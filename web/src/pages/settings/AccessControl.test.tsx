import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AccessControl } from "./AccessControl";

vi.mock("../../lib/auth", () => ({
  useAuth: () => ({ token: "t", role: "admin" })
}));

vi.mock("../../hooks/useGoogleIdentityServices", () => ({
  useGoogleIdentityServices: () => ({
    status: "ready",
    loadError: null,
    requestIdToken: () => Promise.resolve("id-token"),
    renderButton: vi.fn()
  })
}));

const json = (body: unknown) => Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));

let users: { id: string; username: string; role: string; disabled: boolean; has_password: boolean }[];
let admins: string[];
const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  if (url.endsWith("/auth/roles")) return json({ roles: [] });
  if (url.endsWith("/auth/users") && init?.method === "POST") {
    const body = JSON.parse(String(init.body)) as { username: string; role: string };
    users.push({ id: "u2", username: body.username, role: body.role, disabled: false, has_password: false });
    admins = [body.username];
    return json(users[users.length - 1]);
  }
  if (url.endsWith("/auth/users")) return json(users);
  if (url.endsWith("/auth/settings/google/test")) {
    return json({ ok: true, email: "me@company.com", message: "Verified as me@company.com." });
  }
  if (url.endsWith("/auth/settings")) {
    return json({
      password_login_enabled: true,
      google_enabled: true,
      google_client_id: "client.apps.googleusercontent.com",
      google_hosted_domain: "company.com",
      google_auto_provision: false,
      google_admin_emails: admins
    });
  }
  return json({});
});

const renderPage = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AccessControl />
    </QueryClientProvider>
  );

describe("AccessControl Google admins", () => {
  beforeEach(() => {
    users = [{ id: "u1", username: "root", role: "admin", disabled: false, has_password: true }];
    admins = [];
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockClear();
  });

  it("warns when no admin can sign in with Google and adds one by email", async () => {
    renderPage();
    expect(await screen.findByText("No admin can sign in with Google yet")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Admin Google email"), { target: { value: "Boss@Company.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Add as admin" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/auth/users",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ username: "boss@company.com", password: null, role: "admin" })
        })
      )
    );
    expect(await screen.findByRole("list", { name: "Google admins" })).toHaveTextContent("boss@company.com");
  });

  it("offers to add the tested Google account as admin", async () => {
    renderPage();
    const testButton = await screen.findByRole("button", { name: "Test configuration" });
    await waitFor(() => expect(testButton).toBeEnabled());
    fireEvent.click(testButton);

    fireEvent.click(await screen.findByRole("button", { name: "Add me@company.com as admin" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/auth/users",
        expect.objectContaining({ body: JSON.stringify({ username: "me@company.com", password: null, role: "admin" }) })
      )
    );
  });

  it("marks users by sign-in method", async () => {
    users.push({ id: "u3", username: "g@company.com", role: "viewer", disabled: false, has_password: false });
    renderPage();
    expect(await screen.findByText("g@company.com")).toBeInTheDocument();
    expect(screen.getByText("Google")).toBeInTheDocument();
    expect(screen.getByText("Password")).toBeInTheDocument();
  });
});
