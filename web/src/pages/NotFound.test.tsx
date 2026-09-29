import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { NotFound } from "./NotFound";

const navigateMock = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return {
    ...actual,
    useNavigate: () => navigateMock
  };
});

const renderAt = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <NotFound />
    </MemoryRouter>
  );

describe("NotFound page", () => {
  beforeEach(() => {
    navigateMock.mockReset();
  });

  it("shows the unmatched path and recovery actions", () => {
    renderAt("/no/such/route");

    expect(screen.getByText("Page not found")).toBeInTheDocument();
    expect(screen.getByText("/no/such/route")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Go to Datalake health/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Go back/i })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Other destinations" })).toBeInTheDocument();
  });

  it("navigates to the search page with the entered query on submit", () => {
    renderAt("/no/such/route");

    fireEvent.change(screen.getByLabelText("Search catalogs, tables, and columns"), {
      target: { value: "orders" }
    });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    expect(navigateMock).toHaveBeenCalledWith("/tables?q=orders");
  });

  it("navigates to the bare search page when submitted with an empty query", () => {
    renderAt("/no/such/route");

    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    expect(navigateMock).toHaveBeenCalledWith("/tables");
  });
});
