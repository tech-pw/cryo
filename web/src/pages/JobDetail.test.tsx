import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ArtifactBlock, formatJobFileCount } from "./JobDetail";

const writeText = vi.fn();

describe("JobDetail file counts", () => {
  it("does not turn a missing file count into zero", () => {
    expect(formatJobFileCount(null)).toBe("Not measured");
    expect(formatJobFileCount(undefined)).toBe("Not measured");
    expect(formatJobFileCount(0)).toBe("0");
    expect(formatJobFileCount(4)).toBe("4");
  });
});

describe("JobDetail ArtifactBlock", () => {
  beforeEach(() => {
    writeText.mockReset();
    writeText.mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText }
    });
  });

  it("provides keyboard-focusable copy and wrap controls", () => {
    render(
      <ArtifactBlock
        label="SQL statement 1"
        content={"SELECT *\nFROM prod.table"}
        fileName="sql.txt"
      />
    );

    const artifact = screen.getByLabelText("SQL statement 1");
    expect(artifact).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("button", { name: "Scroll" })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenCalledWith("SELECT *\nFROM prod.table");

    fireEvent.click(screen.getByRole("button", { name: "Scroll" }));
    expect(screen.getByRole("button", { name: "Wrap" })).toHaveAttribute("aria-pressed", "false");
  });
});
