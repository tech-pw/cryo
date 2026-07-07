import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ErrorBoundary } from "./ErrorBoundary";

describe("ErrorBoundary", () => {
  it("renders children when no error", () => {
    render(
      <ErrorBoundary>
        <div>hello</div>
      </ErrorBoundary>
    );
    expect(screen.getByText("hello")).toBeInTheDocument();
  });

  it("catches errors thrown during render and shows fallback", () => {
    const Broken = () => {
      throw new Error("boom");
    };
    render(
      <ErrorBoundary>
        <Broken />
      </ErrorBoundary>
    );
    expect(screen.getAllByRole("alert").some((el) => /something went wrong/i.test(el.textContent ?? ""))).toBe(true);
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });

  it("getDerivedStateFromError returns the error in state", () => {
    const state = ErrorBoundary.getDerivedStateFromError(new Error("nope"));
    expect(state).toEqual({ error: expect.any(Error) });
    expect((state.error as Error).message).toBe("nope");
  });
});