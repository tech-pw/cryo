import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "./AppShell";

vi.mock("../../lib/auth", () => ({
  useAuth: () => ({ role: "viewer", devTokenEnabledInProd: false })
}));

vi.mock("../assistant/AskCryoDrawer", () => ({
  AskCryoDrawer: () => null
}));

vi.mock("../assistant/AssistantProvider", () => ({
  AssistantProvider: ({ children }: { children: ReactNode }) => children,
  useAssistant: () => ({ openAssistant: vi.fn(), toggle: vi.fn() })
}));

vi.mock("./CommandPalette", () => ({
  CommandPalette: () => null
}));

vi.mock("./Topbar", () => ({
  Topbar: () => null
}));

const renderShell = () =>
  render(
    <MemoryRouter>
      <AppShell>
        <div>content</div>
      </AppShell>
    </MemoryRouter>
  );

const shell = () => document.querySelector(".cryo-shell");

describe("AppShell sidebar shortcut", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("toggles the icon-only sidebar with meta and ctrl B and persists the choice", () => {
    renderShell();
    expect(shell()).toHaveAttribute("data-sidebar-collapsed", "false");
    expect(shell()).not.toHaveClass("cryo-shell--sidebar-collapsed");
    expect(screen.getByRole("link", { name: "Home" })).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "b", metaKey: true });
    expect(shell()).toHaveClass("cryo-shell--sidebar-collapsed");
    expect(shell()).toHaveAttribute("data-sidebar-collapsed", "true");
    expect(window.localStorage.getItem("cryo.sidebar.collapsed")).toBe("1");
    expect(screen.getByRole("link", { name: "Home" })).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "b", ctrlKey: true });
    expect(shell()).not.toHaveClass("cryo-shell--sidebar-collapsed");
    expect(shell()).toHaveAttribute("data-sidebar-collapsed", "false");
    expect(window.localStorage.getItem("cryo.sidebar.collapsed")).toBe("0");
  });

  it("restores a collapsed sidebar from localStorage", () => {
    window.localStorage.setItem("cryo.sidebar.collapsed", "1");
    renderShell();
    expect(shell()).toHaveClass("cryo-shell--sidebar-collapsed");
    expect(shell()).toHaveAttribute("data-sidebar-collapsed", "true");
  });

  it("still toggles when localStorage throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("denied");
    });
    renderShell();
    expect(shell()).toHaveAttribute("data-sidebar-collapsed", "false");
    fireEvent.keyDown(window, { key: "B", metaKey: true });
    expect(shell()).toHaveAttribute("data-sidebar-collapsed", "true");
  });
});
