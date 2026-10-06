import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BeforeAfter } from "./BeforeAfter";

describe("BeforeAfter", () => {
  it("shows not reported instead of zero when a side is null", () => {
    const { rerender } = render(<BeforeAfter label="Small files" before={12} after={null} unit="files" />);
    expect(screen.getByTestId("before-after")).toHaveTextContent("12 files → not reported");
    expect(screen.getByText("not reported")).toBeInTheDocument();
    expect(screen.getByTestId("before-after-after")).toHaveTextContent("not reported");
    expect(screen.getByTestId("before-after-after")).not.toHaveTextContent("0");

    rerender(<BeforeAfter label="Small files" before={null} after={4} unit="files" />);
    expect(screen.getByTestId("before-after")).toHaveTextContent("not reported → 4 files");
    expect(screen.getByTestId("before-after-before")).toHaveTextContent("not reported");
    expect(screen.getByTestId("before-after-before")).not.toHaveTextContent("0");
  });

  it("reads not reported on the byte line when bytes are unreported", () => {
    render(<BeforeAfter label="Small files" before={12} after={4} unit="files" bytesUnreported />);
    expect(screen.getByTestId("before-after")).toHaveTextContent("12 files → 4 files");
    expect(screen.getByTestId("before-after-bytes")).toHaveTextContent("not reported");
    expect(screen.getByText("not reported")).toBeInTheDocument();
    expect(screen.getByTestId("before-after-bytes")).not.toHaveTextContent("0");
  });

  it("keeps a measured zero", () => {
    render(<BeforeAfter label="Small files" before={0} after={4} unit="files" />);
    expect(screen.getByTestId("before-after-before")).toHaveTextContent("0 files");
    expect(screen.queryByText("not reported")).not.toBeInTheDocument();
  });
});
