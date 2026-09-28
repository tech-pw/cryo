import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { EventMarkers } from "./EventMarkers";

const events = [
  { id: "m", at: "2026-09-01T00:00:00Z", kind: "maintenance" as const, label: "Optimize" },
  { id: "s", at: "2026-09-02T00:00:00Z", kind: "schema" as const, label: "Add column" },
  { id: "r", at: "2026-09-03T00:00:00Z", kind: "restore" as const, label: "Rollback" }
];

describe("EventMarkers", () => {
  it("renders keyboard-reachable markers, not a second series", () => {
    const onSelect = vi.fn();
    render(<EventMarkers events={events} onSelect={onSelect} />);
    const maintenance = screen.getByRole("button", { name: /Maintenance/ });
    const schema = screen.getByRole("button", { name: /Schema change/ });
    const restore = screen.getByRole("button", { name: /Restore/ });
    expect(maintenance.tagName).toBe("BUTTON");
    maintenance.focus();
    expect(maintenance).toHaveFocus();
    fireEvent.click(restore);
    expect(onSelect).toHaveBeenCalledWith(events[2]);
    expect(schema).toHaveTextContent("Add column");
    expect(screen.getByRole("list", { name: "Event markers" })).toBeInTheDocument();
  });

  it("renders nothing when there are no events", () => {
    render(<EventMarkers events={[]} />);
    expect(screen.queryByRole("list", { name: "Event markers" })).not.toBeInTheDocument();
  });
});
