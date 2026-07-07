import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// react-window is heavy and DOM-incompatible for jsdom — verify the wrapper
// component contract instead by mocking the library.
vi.mock("react-window", () => ({
  FixedSizeList: ({ height, itemCount, itemSize, width, children }: any) => (
    <div
      data-testid="virtual-list"
      data-height={height}
      data-item-count={itemCount}
      data-item-size={itemSize}
      data-width={width}
    >
      {Array.from({ length: itemCount }).map((_, index) => (
        <div key={index}>{children({ index, style: {} })}</div>
      ))}
    </div>
  )
}));

import { VirtualizedTable } from "./VirtualizedTable";

describe("VirtualizedTable", () => {
  it("renders empty state when there are no rows", () => {
    const { getByText } = render(
      <VirtualizedTable
        rows={[]}
        rowKey={(r: { id: string }) => r.id}
        rowHeight={32}
        height={400}
        renderRow={(r: { id: string }) => <span>{r.id}</span>}
        emptyState={<div>nothing here</div>}
      />
    );
    expect(getByText("nothing here")).toBeTruthy();
  });

  it("forwards height/itemSize/itemCount to FixedSizeList", () => {
    const rows = [{ id: "a" }, { id: "b" }, { id: "c" }];
    const { getByTestId } = render(
      <VirtualizedTable
        rows={rows}
        rowKey={(r: { id: string }) => r.id}
        rowHeight={48}
        height={500}
        renderRow={(r: { id: string }) => <span>{r.id}</span>}
      />
    );
    const list = getByTestId("virtual-list");
    expect(list.dataset.height).toBe("500");
    expect(list.dataset.itemSize).toBe("48");
    expect(list.dataset.itemCount).toBe("3");
    expect(list.dataset.width).toBe("100%");
  });

  it("renders an optional header above the list", () => {
    const { getByText } = render(
      <VirtualizedTable
        rows={[{ id: "x" }]}
        rowKey={(r: { id: string }) => r.id}
        rowHeight={32}
        height={200}
        renderRow={(r: { id: string }) => <span>{r.id}</span>}
        header={<div>My Header</div>}
      />
    );
    expect(getByText("My Header")).toBeTruthy();
  });
});