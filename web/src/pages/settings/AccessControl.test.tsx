import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AccessControl } from "./AccessControl";

vi.mock("../../lib/auth", () => ({
  useAuth: () => ({ api: { token: "t" }, token: "t", role: "admin", subject: "admin", loading: false, error: null, isAuthenticated: true })
}));

vi.mock("../../hooks/useGoogleIdentityServices", () => ({
  useGoogleIdentityServices: () => ({ status: "idle", loadError: null, requestIdToken: vi.fn() })
}));

const json = (body: unknown) => Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));

describe("AccessControl", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn((path: string, init?: RequestInit) => {
      if (path === "/api/v1/auth/users" && !init?.method) {
        return json([{ id: "u1", username: "maya", role: "operator", disabled: false, has_password: true }]);
      }
      if (path === "/api/v1/auth/roles") return json({ roles: [] });
      if (path === "/api/v1/auth/settings") {
        return json({ password_login_enabled: true, google_enabled: false, google_client_id: null, google_hosted_domain: null, google_auto_provision: false });
      }
      return json({});
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => vi.unstubAllGlobals());

  const patches = () => fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === "PATCH");

  it("asks before disabling a user and only then sends the PATCH", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <AccessControl />
      </QueryClientProvider>
    );

    fireEvent.click(await screen.findByRole("button", { name: "Disable" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText("Disable maya?")).toBeInTheDocument();
    expect(patches()).toHaveLength(0);

    fireEvent.click(within(dialog).getByRole("button", { name: "Disable user" }));
    await waitFor(() => expect(patches()).toHaveLength(1));
    const [path, init] = patches()[0] as [string, RequestInit];
    expect(path).toBe("/api/v1/auth/users/u1");
    expect(JSON.parse(String(init.body))).toMatchObject({ disabled: true });
  });

  it("does nothing when the dialog is cancelled", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <AccessControl />
      </QueryClientProvider>
    );

    fireEvent.click(await screen.findByRole("button", { name: "Disable" }));
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(patches()).toHaveLength(0);
  });
});
