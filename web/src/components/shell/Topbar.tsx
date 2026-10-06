import { useQuery } from "@tanstack/react-query";
import { Layers, Moon, Search, Sparkles, Sun, User } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { getAssistantCapabilities } from "../../api/assistant";
import { Button } from "../ui/button";
import { useAssistant } from "../assistant/AssistantProvider";
import { useAuth } from "../../lib/auth";
import { loadDraft, MAINTENANCE_BATCH_CHANGE_EVENT, membershipSummary } from "../../lib/maintenanceBatch";
import { useTheme } from "../../lib/themeContext";
import { CatalogSearch } from "./CatalogSearch";
import { useShell } from "./ShellContext";

export interface TopbarProps {
  onMenuClick?: () => void;
  menuOpen?: boolean;
}

export const Topbar = ({ onMenuClick, menuOpen = false }: TopbarProps) => {
  const { subject, role, signOut, api, token } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const { openCommandPalette, openAssistant } = useShell();
  const { openBatch } = useAssistant();
  const batchIdentity = {
    installationId: "local",
    principalId: subject || "anonymous",
    tenantId: "local"
  };
  const [batchCount, setBatchCount] = useState(() => membershipSummary(loadDraft(batchIdentity)));
  useEffect(() => {
    const reload = () => setBatchCount(membershipSummary(loadDraft(batchIdentity)));
    reload();
    window.addEventListener(MAINTENANCE_BATCH_CHANGE_EVENT, reload);
    return () => window.removeEventListener(MAINTENANCE_BATCH_CHANGE_EVENT, reload);
  }, [subject]);
  const capabilities = useQuery({
    queryKey: ["assistant", "capabilities", token],
    queryFn: () => getAssistantCapabilities(api),
    staleTime: 60_000,
    retry: false
  });
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const userMenuRef = useRef<HTMLDivElement>(null);
  const userTriggerRef = useRef<HTMLButtonElement>(null);
  const signOutRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!userMenuOpen) return;
    signOutRef.current?.focus();
    const onPointerDown = (event: MouseEvent) => {
      if (!userMenuRef.current?.contains(event.target as Node)) {
        setUserMenuOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setUserMenuOpen(false);
      userTriggerRef.current?.focus();
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
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
          aria-label={menuOpen ? "Close navigation menu" : "Open navigation menu"}
          aria-expanded={menuOpen}
          aria-controls="primary-navigation"
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
        <Button type="button" variant="secondary" size="sm" onClick={openBatch} aria-label={`Review batch, ${batchCount}`} title={`Review batch: ${batchCount}`} className="cryo-shell__batch-button">
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
            ref={userTriggerRef}
            type="button"
            className="cryo-shell__user-trigger"
            aria-label={`${subject} · ${role}`}
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
                ref={signOutRef}
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
