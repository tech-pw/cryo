import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AccessControl } from "./AccessControl";

vi.mock("../../lib/auth", () => ({
  useAuth: () => ({ token: "t", role: "admin", subject: "root" })
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

interface TestUser {
  id: string;
  username: string;
  role: string;
  disabled: boolean;
  has_password: boolean;
  created_via?: string | null;
  last_login_at?: string | null;
  last_login_method?: string | null;
}

let users: TestUser[];
let admins: string[];
let hostedDomain: string | null;
let autoProvision: boolean;
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
  if (url.includes("/auth/users/") && init?.method === "PATCH") return json({});
  if (url.includes("/auth/audit")) {
    return json([
      { ts: new Date().toISOString(), action: "user_updated", actor: "root", detail: "g@company.com: role=viewer->operator" }
    ]);
  }
  if (url.endsWith("/auth/settings/google/test")) {
    return json({ ok: true, email: "me@company.com", message: "Verified as me@company.com." });
  }
  if (url.endsWith("/auth/settings")) {
    if (init?.method === "PATCH") {
      const body = JSON.parse(String(init.body)) as { google_auto_provision?: boolean };
      if (body.google_auto_provision !== undefined) autoProvision = body.google_auto_provision;
    }
    return json({
      password_login_enabled: true,
      google_enabled: true,
      google_client_id: "client.apps.googleusercontent.com",
      google_hosted_domain: hostedDomain,
      google_auto_provision: autoProvision,
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
    hostedDomain = "company.com";
    autoProvision = false;
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

  it("turns on viewer access for new accounts on the saved domain", async () => {
    renderPage();
    const toggle = await screen.findByRole("switch", { name: "Give new Google accounts viewer access" });
    await waitFor(() => expect(toggle).toBeEnabled());

    fireEvent.click(toggle);

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/auth/settings",
        expect.objectContaining({ method: "PATCH", body: JSON.stringify({ google_auto_provision: true }) })
      )
    );
    await waitFor(() => expect(toggle).toBeChecked());
  });

  it("keeps viewer auto-access off until a domain is saved", async () => {
    hostedDomain = null;
    renderPage();
    const toggle = await screen.findByRole("switch", { name: "Give new Google accounts viewer access" });
    expect(await screen.findByText(/Save a domain restriction first/)).toBeInTheDocument();
    expect(toggle).toBeDisabled();
  });

  it("shows last sign-in and how each person joined", async () => {
    users.push({
      id: "u3",
      username: "g@company.com",
      role: "viewer",
      disabled: false,
      has_password: false,
      created_via: "google",
      last_login_at: new Date(Date.now() - 2 * 3_600_000).toISOString(),
      last_login_method: "google"
    });
    renderPage();
    expect(await screen.findByText("Last sign-in 2h ago with Google · Joined with Google")).toBeInTheDocument();
    expect(screen.getByText("Never signed in")).toBeInTheDocument();
  });

  it("asks before disabling someone and never lets admins lock themselves out", async () => {
    users.push({ id: "u3", username: "g@company.com", role: "viewer", disabled: false, has_password: false });
    renderPage();
    await screen.findByText("g@company.com");
    expect(screen.getByLabelText("Role for root")).toBeDisabled();

    const disableButtons = screen.getAllByRole("button", { name: "Disable" });
    expect(disableButtons[0]).toBeDisabled();
    fireEvent.click(disableButtons[1]);
    expect(await screen.findByText("Disable g@company.com?")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalledWith("/api/v1/auth/users/u3", expect.anything());

    fireEvent.click(screen.getByRole("button", { name: "Disable" }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/auth/users/u3",
        expect.objectContaining({ method: "PATCH", body: JSON.stringify({ disabled: true }) })
      )
    );
  });

  it("lists recent access changes", async () => {
    renderPage();
    const list = await screen.findByRole("list", { name: "Recent access changes" });
    expect(list).toHaveTextContent("Changed access");
    expect(list).toHaveTextContent("g@company.com: role=viewer->operator");
  });
});
