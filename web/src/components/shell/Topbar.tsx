import { useQuery } from "@tanstack/react-query";
import { Layers, Moon, Search, Sparkles, Sun, User } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { getAssistantCapabilities } from "../../api/assistant";
import { Button } from "../ui/button";
import { useAssistant } from "../assistant/AssistantProvider";
import { useAuth } from "../../lib/auth";
import { loadDraft, membershipSummary } from "../../lib/maintenanceBatch";
import { useTheme } from "../../lib/themeContext";
import { CatalogSearch } from "./CatalogSearch";
import { useShell } from "./ShellContext";

export interface TopbarProps {
  onMenuClick?: () => void;
}

export const Topbar = ({ onMenuClick }: TopbarProps) => {
  const { subject, role, signOut, api, token } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const { openCommandPalette, openAssistant } = useShell();
  const { openBatch } = useAssistant();
  const batchCount = membershipSummary(
    loadDraft({
      installationId: "local",
      principalId: subject || "anonymous",
      tenantId: "local"
    })
  );
  const capabilities = useQuery({
    queryKey: ["assistant", "capabilities", token],
    queryFn: () => getAssistantCapabilities(api),
    staleTime: 60_000,
    retry: false
  });
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const userMenuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!userMenuOpen) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!userMenuRef.current?.contains(event.target as Node)) {
        setUserMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [userMenuOpen]);

  const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad|iPod/.test(navigator.platform);
  const cmdLabel = isMac ? "⌘K" : "Ctrl+K";
  const askLabel = isMac ? "⌘I" : "Ctrl+I";
  const askHidden = capabilities.isSuccess && capabilities.data.enabled === false;

  return (
    <header
      className="cryo-shell__topbar"
      style={{
        background: "var(--color-bg)",
        borderBottom: "1px solid var(--color-border-default)"
      }}
    >
      <div className="cryo-shell__topbar-left">
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="cryo-shell__menu-btn"
          aria-label="Open navigation menu"
          onClick={onMenuClick}
        >
          <span aria-hidden>☰</span>
        </Button>
      </div>

      <div className="cryo-shell__topbar-center">
        <CatalogSearch />
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={openCommandPalette}
          aria-label="Open command palette"
          title={`Command palette (${cmdLabel})`}
        >
          <Search size={14} aria-hidden />
          <span className="cryo-shell__kbd">{cmdLabel}</span>
        </Button>
      </div>

      <div className="cryo-shell__topbar-right">
        <Button type="button" variant="secondary" size="sm" onClick={openBatch} aria-label="Review batch" title={`Review batch: ${batchCount}`} className="cryo-shell__batch-button">
          <Layers size={14} aria-hidden className="cryo-shell__batch-icon" /><span>Batch · {batchCount}</span>
        </Button>
        {askHidden ? null : (
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={() => openAssistant()}
            aria-label="Ask Cryo"
            title={`Ask Cryo (${askLabel})`}
          >
            <Sparkles size={14} aria-hidden />
            <span className="cryo-shell__ask-label">Ask Cryo</span>
          </Button>
        )}
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={toggleTheme}
          aria-label={theme === "light" ? "Switch to dark mode" : "Switch to light mode"}
        >
          {theme === "light" ? <Moon size={16} /> : <Sun size={16} />}
        </Button>

        <div className="cryo-shell__user-menu" ref={userMenuRef}>
          <button
            type="button"
            className="cryo-shell__user-trigger"
            aria-expanded={userMenuOpen}
            aria-haspopup="menu"
            onClick={() => setUserMenuOpen((open) => !open)}
          >
            <User size={16} aria-hidden />
            <span>
              {subject} · {role}
            </span>
          </button>
          {userMenuOpen ? (
            <div className="cryo-shell__user-dropdown" role="menu">
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setUserMenuOpen(false);
                  signOut();
                }}
              >
                Sign out
              </button>
            </div>
          ) : null}
        </div>
      </div>
    </header>
  );
};
