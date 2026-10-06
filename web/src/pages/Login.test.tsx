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
  devTokenEnabledInProd: false
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
