import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider, SESSION_ENDED_MESSAGE, useAuth } from "./auth";

const encode = (value: object) =>
  btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");

const localToken = (expSeconds: number) =>
  `${encode({ alg: "HS256" })}.${encode({ sub: "g@company.com", roles: ["viewer"], exp: expSeconds })}.sig`;

const Probe = () => {
  const { role, subject, error, isAuthenticated } = useAuth();
  return (
    <div>
      <span data-testid="role">{role}</span>
      <span data-testid="subject">{subject}</span>
      <span data-testid="authenticated">{String(isAuthenticated)}</span>
      <span data-testid="error">{error ?? ""}</span>
    </div>
  );
};

const response = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status }));

let meStatus: number;
let meRole: string;
const fetchMock = vi.fn((input: RequestInfo | URL) => {
  const url = String(input);
  if (url.endsWith("/auth/config")) {
    return response(200, {
      mode: "local",
      dev_token_enabled: false,
      dev_token_enabled_in_prod: false,
      password_login_enabled: true,
      google: { client_id: "client.apps.googleusercontent.com", hosted_domain: "company.com", auto_provision: true }
    });
  }
  if (url.endsWith("/auth/me")) {
    return response(meStatus, { subject: "g@company.com", role: meRole, managed: true });
  }
  return response(404, {});
});

const signInStoredSession = (token: string) => {
  window.localStorage.setItem("cryo.accessToken", token);
  window.localStorage.setItem("cryo.devSubject", "g@company.com");
  window.localStorage.setItem("cryo.authMode", "local");
};

describe("AuthProvider local sessions", () => {
  beforeEach(() => {
    window.localStorage.clear();
    meStatus = 200;
    meRole = "viewer";
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockClear();
    window.localStorage.clear();
  });

  it("picks up a role an admin granted after sign-in", async () => {
    signInStoredSession(localToken(Math.floor(Date.now() / 1000) + 3600));
    meRole = "admin";
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );
    await waitFor(() => expect(screen.getByTestId("role")).toHaveTextContent("admin"));
    expect(window.localStorage.getItem("cryo.devRole")).toBe("admin");
  });

  it("signs out with a clear message when the account is disabled", async () => {
    signInStoredSession(localToken(Math.floor(Date.now() / 1000) + 3600));
    meStatus = 401;
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );
    await waitFor(() => expect(screen.getByTestId("error")).toHaveTextContent(SESSION_ENDED_MESSAGE));
    expect(screen.getByTestId("authenticated")).toHaveTextContent("false");
    expect(window.localStorage.getItem("cryo.accessToken")).toBeNull();
  });

  it("signs out when the token has expired without asking the server", async () => {
    signInStoredSession(localToken(Math.floor(Date.now() / 1000) - 10));
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );
    await waitFor(() => expect(screen.getByTestId("error")).toHaveTextContent(SESSION_ENDED_MESSAGE));
    expect(fetchMock).not.toHaveBeenCalledWith("/api/v1/auth/me", expect.anything());
  });

  it("rechecks the session when the window regains focus", async () => {
    signInStoredSession(localToken(Math.floor(Date.now() / 1000) + 3600));
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );
    await waitFor(() => expect(screen.getByTestId("role")).toHaveTextContent("viewer"));
    meRole = "admin";
    act(() => {
      window.dispatchEvent(new Event("focus"));
    });
    await waitFor(() => expect(screen.getByTestId("role")).toHaveTextContent("admin"));
  });
});
