import { fireEvent, render, screen } from "@testing-library/react";
import { Button } from "antd";
import { describe, expect, it, vi } from "vitest";
import { ConfirmAction } from "./confirm";

describe("ConfirmAction", () => {
  it("requires confirmation before running the destructive handler", async () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmAction title="Delete item?" description="This cannot be undone." onConfirm={onConfirm}>
        <Button danger>Delete</Button>
      </ConfirmAction>
    );

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(onConfirm).not.toHaveBeenCalled();

    fireEvent.click(await screen.findByRole("button", { name: "Confirm" }));
    expect(onConfirm).toHaveBeenCalledOnce();
  });
});
