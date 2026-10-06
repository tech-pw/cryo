import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DevTokenProdBanner } from "./DevTokenProdBanner";

const authMock = vi.hoisted(() => ({
  devTokenEnabledInProd: false
}));

vi.mock("../../lib/auth", () => ({
  useAuth: () => authMock
}));

describe("DevTokenProdBanner", () => {
  beforeEach(() => {
    authMock.devTokenEnabledInProd = false;
  });

  it("stays hidden when development sign-in is not enabled in production", () => {
    const { container } = render(<DevTokenProdBanner />);
    expect(container).toBeEmptyDOMElement();
    expect(
      screen.queryByText(/development sign-in is enabled on this production install/i)
    ).not.toBeInTheDocument();
  });

  it("shows a persistent warning while development sign-in is enabled in production", () => {
    authMock.devTokenEnabledInProd = true;
    render(<DevTokenProdBanner />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(
      screen.getByText(/development sign-in is enabled on this production install/i)
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Dismiss" })).not.toBeInTheDocument();
  });
});
