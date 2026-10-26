import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RemovedFromCatalogBanner } from "./RemovedFromCatalogBanner";

describe("RemovedFromCatalogBanner", () => {
  it("renders nothing for a live table", () => {
    const { container } = render(<RemovedFromCatalogBanner removedAt={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("explains a tombstoned table and shows the reason", () => {
    render(
      <RemovedFromCatalogBanner
        removedAt="2026-10-26T06:00:00Z"
        reason="not_found_in_catalog: lake/sales"
      />
    );
    expect(screen.getByText(/Removed from catalog/)).toBeInTheDocument();
    expect(screen.getByText(/next scan restores/)).toBeInTheDocument();
    expect(screen.getByText("not_found_in_catalog: lake/sales")).toBeInTheDocument();
  });
});
