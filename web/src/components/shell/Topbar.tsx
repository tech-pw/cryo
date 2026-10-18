import { useQuery } from "@tanstack/react-query";
import { Layers, Menu, Moon, Search, Sparkles, Sun, User } from "lucide-react";
import { useEffect, useState } from "react";

import { getAssistantCapabilities } from "../../api/assistant";
import { Button } from "../ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger
} from "../ui/dropdown-menu";
import { Tooltip, TooltipContent, TooltipTrigger } from "../ui/tooltip";
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
          <Menu size={16} aria-hidden />
        </Button>
      </div>

      <div className="cryo-shell__topbar-center">
        <CatalogSearch />
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={openCommandPalette}
              aria-label="Open command palette"
            >
              <Search size={14} aria-hidden />
              <span className="cryo-shell__kbd">{cmdLabel}</span>
            </Button>
          </TooltipTrigger>
          <TooltipContent>{`Command palette (${cmdLabel})`}</TooltipContent>
        </Tooltip>
      </div>

      <div className="cryo-shell__topbar-right">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={openBatch}
              aria-label={`Review batch, ${batchCount}`}
              className="cryo-shell__batch-button"
            >
              <Layers size={14} aria-hidden className="cryo-shell__batch-icon" />
              <span>Batch · {batchCount}</span>
            </Button>
          </TooltipTrigger>
          <TooltipContent>{`Review batch: ${batchCount}`}</TooltipContent>
        </Tooltip>
        {askHidden ? null : (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => openAssistant()}
                aria-label="Ask Cryo"
              >
                <Sparkles size={14} aria-hidden />
                <span className="cryo-shell__ask-label">Ask Cryo</span>
              </Button>
            </TooltipTrigger>
            <TooltipContent>{`Ask Cryo (${askLabel})`}</TooltipContent>
          </Tooltip>
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

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button type="button" className="cryo-shell__user-trigger" aria-label={`${subject} · ${role}`}>
              <User size={16} aria-hidden />
              <span>
                {subject} · {role}
              </span>
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" sideOffset={6} className="w-44">
            <DropdownMenuItem onSelect={() => signOut()}>Sign out</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
};
