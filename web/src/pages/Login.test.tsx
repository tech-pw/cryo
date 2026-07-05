import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Login } from "./Login";

vi.mock("../lib/auth", () => ({
  useAuth: () => ({
    signIn: vi.fn(),
    startOidcSignIn: vi.fn(),
    loading: false,
    error: null,
    authMode: "dev",
    devTokenEnabled: true
  }),
  roleOptions: [
    { label: "viewer", value: "viewer" },
    { label: "operator", value: "operator" },
    { label: "admin", value: "admin" }
  ]
}));

describe("Login page", () => {
  it("renders sign-in form", () => {
    render(<Login />);
    expect(screen.getByText("Sign in to Cryo")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /sign in/i })).toBeInTheDocument();
  });
});
