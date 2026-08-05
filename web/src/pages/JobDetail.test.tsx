import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ArtifactBlock } from "./JobDetail";

const writeText = vi.fn();

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
