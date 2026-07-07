import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PageBreadcrumbs } from "./PageBreadcrumbs";
import { MemoryRouter, Route, Routes } from "react-router-dom";

const renderAt = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="*" element={<PageBreadcrumbs />} />
      </Routes>
    </MemoryRouter>
  );

describe("PageBreadcrumbs", () => {
  it("renders nothing on the root", () => {
    renderAt("/");
    expect(screen.queryByText("Tables")).toBeNull();
  });

  it("renders a labeled breadcrumb for /tables", () => {
    renderAt("/tables");
    expect(document.querySelector('[aria-label="Breadcrumb"]')).toBeInTheDocument();
    const nav = document.querySelector('[aria-label="Breadcrumb"]') as HTMLElement;
    expect(nav.textContent).toContain("Tables");
  });

  it("labels nested routes with the route map", () => {
    renderAt("/tables/abc-123");
    const nav = document.querySelector('[aria-label="Breadcrumb"]') as HTMLElement;
    expect(nav.textContent).toContain("Tables");
    expect(nav.textContent).toContain("abc-123");
  });
});