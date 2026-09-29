import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { LakeZone, type LakeZoneProps } from "./LakeZone";

const base: LakeZoneProps = {
  tracked: 100,
  distribution: [
    { grade: "A", count: 80 },
    { grade: "D", count: 12 },
    { grade: "F", count: 3 }
  ],
  loading: false,
  error: false,
  sizeGb: 12.5,
  smallFiles: 40,
  needsCompaction: 2,
  reclaimableBytes: 1024 ** 3,
  previousDf: null,
  hotDf: null
};

const renderZone = (overrides: Partial<LakeZoneProps> = {}) =>
  render(
    <MemoryRouter>
      <LakeZone {...base} {...overrides} />
    </MemoryRouter>
  );

describe("LakeZone", () => {
  it("links the D/F count to /catalogs?grades=D,F", () => {
    renderZone();
    const link = screen.getByRole("link", { name: /tables at D or F/i });
    expect(link).toHaveAttribute("href", "/catalogs?grades=D,F");
    expect(link.className).not.toMatch(/text-primary|text-\[var\(--color-link\)\]/);
    expect(link.querySelector(".font-semibold")).toHaveClass("underline");
  });

  it("does not render tracked tables as its own tile", () => {
    renderZone();
    expect(screen.queryByText("Tracked tables")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
    expect(screen.getByText("Data size")).toBeInTheDocument();
  });

  it("labels reclaimable stock as Reclaimable now", () => {
    renderZone();
    expect(screen.getByText("Reclaimable now")).toBeInTheDocument();
  });

  it("shows Not measured for a null data size", () => {
    renderZone({ sizeGb: null });
    expect(screen.getByText("Not measured")).toBeInTheDocument();
    expect(screen.queryByText("0 GB")).not.toBeInTheDocument();
  });

  it("omits the yesterday chip when previousDf is null", () => {
    renderZone({ previousDf: null });
    expect(screen.queryByText(/since yesterday/)).not.toBeInTheDocument();
  });

  it("renders a worse delta outside the sentence link and a better delta with a minus sign", () => {
    const { rerender } = renderZone({ previousDf: 10 });
    const worse = screen.getByText("+5 since yesterday");
    expect(worse).toHaveClass("text-[var(--color-status-danger)]");
    expect(screen.getByRole("link", { name: /tables at D or F/i })).not.toContainElement(worse);

    rerender(
      <MemoryRouter>
        <LakeZone {...base} previousDf={20} />
      </MemoryRouter>
    );
    expect(screen.getByText("\u22125 since yesterday")).toHaveClass("text-[var(--color-status-success)]");
  });

  it("omits a zero delta and renders the hot clause only for a number", () => {
    renderZone({ previousDf: 15, hotDf: null });
    expect(screen.queryByText(/since yesterday/)).not.toBeInTheDocument();
    expect(screen.queryByText(/hot$/)).not.toBeInTheDocument();

    renderZone({ hotDf: 4 });
    expect(screen.getByText("4 hot")).toBeInTheDocument();
  });

  it("uses unknown and empty copy without a grade link", () => {
    renderZone({ distribution: null, tracked: 14700 });
    expect(screen.getByText(/Grade split unknown/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /tables at D or F/i })).not.toBeInTheDocument();

    renderZone({ distribution: [], tracked: 0 });
    expect(screen.getByText("No tracked tables")).toBeInTheDocument();
  });

  it("returns nothing on error and does not use 88px card skeletons while loading", () => {
    const { container, rerender } = renderZone({ error: true });
    expect(container).toBeEmptyDOMElement();

    rerender(
      <MemoryRouter>
        <LakeZone {...base} loading />
      </MemoryRouter>
    );
    expect(container.querySelector(".h-\\[88px\\]")).toBeNull();
    expect(screen.getByLabelText("Lake")).toHaveAttribute("aria-busy", "true");
    expect(screen.getByLabelText("Lake")).toBeInTheDocument();
  });
});
