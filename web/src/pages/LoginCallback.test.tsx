import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { LoginCallback } from "./LoginCallback";
import { OIDC_STATE_STORAGE_KEY, OidcExchangeError } from "../lib/auth";

const navigateMock = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return {
    ...actual,
    useNavigate: () => navigateMock
  };
});

const completeOidcSignIn = vi.fn();

vi.mock("../lib/auth", async () => {
  const actual = await vi.importActual<typeof import("../lib/auth")>("../lib/auth");
  return {
    ...actual,
    useAuth: () => ({ completeOidcSignIn })
  };
});

const STATE = "expected-state";

const renderAt = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <LoginCallback />
    </MemoryRouter>
  );

const callbackWithCode = (code = "abc123", state = STATE) =>
  `/auth/callback?code=${code}&state=${state}`;

describe("LoginCallback page", () => {
  beforeEach(() => {
    navigateMock.mockReset();
    completeOidcSignIn.mockReset();
    sessionStorage.clear();
  });

  it("shows a missing-code outcome and no spinner when opened without a code", async () => {
    renderAt("/auth/callback");

    expect(await screen.findByText("Sign-in link is incomplete")).toBeInTheDocument();
    expect(screen.queryByText("Completing sign-in…")).not.toBeInTheDocument();
    expect(completeOidcSignIn).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Return to sign in" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });

  it("navigates home on a successful exchange", async () => {
    sessionStorage.setItem(OIDC_STATE_STORAGE_KEY, STATE);
    completeOidcSignIn.mockResolvedValue(undefined);
    renderAt(callbackWithCode());

    await waitFor(() => expect(completeOidcSignIn).toHaveBeenCalledWith("abc123"));
    expect(sessionStorage.getItem(OIDC_STATE_STORAGE_KEY)).toBeNull();
    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith("/health", { replace: true }));
  });

  it("shows a rejected outcome without retry when the exchange is rejected", async () => {
    sessionStorage.setItem(OIDC_STATE_STORAGE_KEY, STATE);
    completeOidcSignIn.mockRejectedValue(new OidcExchangeError("rejected", "invalid_grant", 400));
    renderAt(callbackWithCode());

    expect(await screen.findByText("Sign-in was rejected")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Return to sign in" })).toBeInTheDocument();
    expect(screen.queryByText("Completing sign-in…")).not.toBeInTheDocument();
  });

  it("shows a network outcome with retry, and retry re-runs the exchange", async () => {
    sessionStorage.setItem(OIDC_STATE_STORAGE_KEY, STATE);
    completeOidcSignIn.mockRejectedValueOnce(new OidcExchangeError("network", "Could not reach the identity provider."));
    renderAt(callbackWithCode());

    expect(await screen.findByText("Couldn't reach the identity provider")).toBeInTheDocument();
    const retryButton = screen.getByRole("button", { name: "Try again" });

    completeOidcSignIn.mockResolvedValueOnce(undefined);
    retryButton.click();

    await waitFor(() => expect(completeOidcSignIn).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith("/health", { replace: true }));
  });

  it("treats unrecognized errors as rejected without exposing raw technical text as the title", async () => {
    sessionStorage.setItem(OIDC_STATE_STORAGE_KEY, STATE);
    completeOidcSignIn.mockRejectedValue(new Error("boom"));
    renderAt(callbackWithCode());

    expect(await screen.findByText("Sign-in was rejected")).toBeInTheDocument();
    expect(screen.getByText("Technical details")).toBeInTheDocument();
  });

  it("shows rejected and does not exchange when state is missing", async () => {
    sessionStorage.setItem(OIDC_STATE_STORAGE_KEY, STATE);
    renderAt("/auth/callback?code=abc123");

    expect(await screen.findByText("Sign-in was rejected")).toBeInTheDocument();
    expect(completeOidcSignIn).not.toHaveBeenCalled();
    expect(sessionStorage.getItem(OIDC_STATE_STORAGE_KEY)).toBeNull();
    expect(screen.queryByText("Technical details")).not.toBeInTheDocument();
  });

  it("shows rejected and does not exchange when state does not match", async () => {
    sessionStorage.setItem(OIDC_STATE_STORAGE_KEY, STATE);
    renderAt("/auth/callback?code=abc123&state=attacker-state");

    expect(await screen.findByText("Sign-in was rejected")).toBeInTheDocument();
    expect(completeOidcSignIn).not.toHaveBeenCalled();
    expect(sessionStorage.getItem(OIDC_STATE_STORAGE_KEY)).toBeNull();
  });

  it("shows rejected and does not exchange when no state was stored", async () => {
    renderAt("/auth/callback?code=abc123&state=attacker-state");

    expect(await screen.findByText("Sign-in was rejected")).toBeInTheDocument();
    expect(completeOidcSignIn).not.toHaveBeenCalled();
  });
});
