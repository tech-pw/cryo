import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CryoTooltipProvider } from "../ui/tooltip";
import { ConsoleShell } from "./ConsoleShell";
import type { LogListItem } from "../LogList";

const items: LogListItem[] = [
  { ts: "2026-01-01T00:00:00Z", level: "info", message: "Discovered 10 tables" },
  { ts: "2026-01-01T00:00:05Z", level: "error", message: "Failed to scan schema foo" },
  { ts: "2026-01-01T00:00:10Z", level: "info", message: "Scan complete" }
];

const renderShell = (props: Partial<React.ComponentProps<typeof ConsoleShell>> = {}) =>
  render(
    <CryoTooltipProvider>
      <ConsoleShell
        items={items}
        wsOpen
        formatTime={(ts) => ts ?? "—"}
        {...props}
      />
    </CryoTooltipProvider>
  );

describe("ConsoleShell log controls", () => {
  it("shows an accessible total count with no filter applied", () => {
    renderShell();
    expect(screen.getByText("3 entries")).toBeInTheDocument();
  });

  it("filters log entries by search text and reports the visible/hidden count", () => {
    renderShell();
    const search = screen.getByLabelText("Search log");
    fireEvent.change(search, { target: { value: "schema foo" } });
    expect(screen.getByText("Showing 1 of 3 entries")).toBeInTheDocument();
    expect(screen.getByText("Failed to scan schema foo")).toBeInTheDocument();
    expect(screen.queryByText("Scan complete")).not.toBeInTheDocument();
  });

  it("filters log entries by level", () => {
    renderShell();
    const levelSelect = screen.getByLabelText("Level");
    fireEvent.change(levelSelect, { target: { value: "error" } });
    expect(screen.getByText("Showing 1 of 3 entries")).toBeInTheDocument();
    expect(screen.getByText("Failed to scan schema foo")).toBeInTheDocument();
  });

  it("copies the filtered log to the clipboard", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    renderShell();
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenCalledTimes(1);
    expect(writeText.mock.calls[0][0]).toContain("Discovered 10 tables");
  });

  it("disables copy/download when the filtered result is empty", () => {
    renderShell();
    const search = screen.getByLabelText("Search log");
    fireEvent.change(search, { target: { value: "no such entry" } });
    expect(screen.getByRole("button", { name: "Copy" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Download" })).toBeDisabled();
    expect(screen.getByText("No log entries match the current filter.")).toBeInTheDocument();
  });
});
