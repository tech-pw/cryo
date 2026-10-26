import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Login } from "./Login";

const authMock = vi.hoisted(() => ({
  signIn: vi.fn(),
  startOidcSignIn: vi.fn(),
  loading: false,
  error: null as string | null,
  authMode: "dev" as "dev" | "oidc",
  devTokenEnabled: true,
  devTokenEnabledInProd: false,
  passwordLoginEnabled: false,
  googleClientId: null as string | null,
  signInWithGoogle: vi.fn(),
  signInWithPassword: vi.fn()
}));

vi.mock("../lib/auth", () => ({
  useAuth: () => authMock,
  roleOptions: [
    { label: "viewer", value: "viewer" },
    { label: "operator", value: "operator" },
    { label: "admin", value: "admin" }
  ]
}));

describe("Login page", () => {
  beforeEach(() => {
    authMock.authMode = "dev";
    authMock.devTokenEnabled = true;
    authMock.devTokenEnabledInProd = false;
    authMock.error = null;
    authMock.loading = false;
    authMock.passwordLoginEnabled = false;
    authMock.googleClientId = null;
  });

  it("offers Google sign-in outside OIDC mode once a Client ID is saved", () => {
    authMock.googleClientId = "client.apps.googleusercontent.com";
    render(<Login />);
    expect(screen.getByTestId("google-sign-in")).toBeInTheDocument();
    expect(screen.getByRole("separator")).toHaveTextContent("or");
  });

  it("keeps password sign-in usable when dev tokens are off", () => {
    authMock.devTokenEnabled = false;
    authMock.passwordLoginEnabled = true;
    render(<Login />);
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
    expect(screen.queryByText("Role")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /sign in with password/i })).toBeEnabled();
    expect(screen.queryByText("Dev sign-in is disabled")).not.toBeInTheDocument();
  });

  it("shows only Google when it is the one sign-in method left", () => {
    authMock.devTokenEnabled = false;
    authMock.googleClientId = "client.apps.googleusercontent.com";
    render(<Login />);
    expect(screen.getByTestId("google-sign-in")).toBeInTheDocument();
    expect(screen.queryByLabelText("Username")).not.toBeInTheDocument();
    expect(screen.queryByText("Dev sign-in is disabled")).not.toBeInTheDocument();
  });

  it("renders sign-in form", () => {
    render(<Login />);
    expect(screen.getByText("Sign in to Cryo")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /sign in/i })).toBeInTheDocument();
  });

  it("says development sign-in is enabled on a production install that still accepts it", () => {
    authMock.devTokenEnabledInProd = true;
    render(<Login />);
    expect(
      screen.getByText(/development sign-in is enabled on this production install/i)
    ).toBeInTheDocument();
    expect(screen.queryByText(/disable the token endpoint/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/dev token issuance is disabled/i)).not.toBeInTheDocument();
  });

  it("keeps the production-disabled copy when development sign-in is off", () => {
    render(<Login />);
    expect(screen.getByText(/production deployments disable the token endpoint/i)).toBeInTheDocument();
  });

  it("does not claim dev token issuance is disabled in oidc mode when production still accepts it", () => {
    authMock.authMode = "oidc";
    authMock.devTokenEnabledInProd = true;
    render(<Login />);
    expect(
      screen.getByText(/development sign-in is enabled on this production install/i)
    ).toBeInTheDocument();
    expect(screen.queryByText(/dev token issuance is disabled/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/disable the token endpoint/i)).not.toBeInTheDocument();
  });
});
