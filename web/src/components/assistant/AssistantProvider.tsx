import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";

export interface AssistantLaunch {
  nonce: number;
  question?: string;
  context?: Record<string, unknown>;
  fresh: boolean;
}

interface AssistantContextValue {
  open: boolean;
  setOpen: (open: boolean) => void;
  toggle: () => void;
  openAssistant: (question?: string, context?: object) => void;
  panel: "ask" | "batch";
  setPanel: (panel: "ask" | "batch") => void;
  openBatch: () => void;
  pending: AssistantLaunch | null;
  conversationId: string | null;
  setConversationId: (id: string | null) => void;
}

const AssistantContext = createContext<AssistantContextValue | null>(null);

let launchNonce = 0;

const asRecord = (context?: object): Record<string, unknown> | undefined => {
  if (!context || typeof context !== "object") return undefined;
  return { ...(context as Record<string, unknown>) };
};

export const AssistantProvider = ({ children }: { children: ReactNode }) => {
  const [open, setOpenState] = useState(false);
  const [panel, setPanel] = useState<"ask" | "batch">("ask");
  const [pending, setPending] = useState<AssistantLaunch | null>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const conversationId = searchParams.get("ask");

  const setOpen = useCallback((next: boolean) => {
    if (next) {
      returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    }
    setOpenState(next);
    if (!next) {
      const back = returnFocus.current;
      window.setTimeout(() => back?.focus(), 0);
    }
  }, []);

  const setConversationId = useCallback(
    (id: string | null) => {
      setSearchParams(
        (prev) => {
          const current = prev.get("ask");
          if ((id ?? null) === (current ?? null)) return prev;
          const next = new URLSearchParams(prev);
          if (id) next.set("ask", id);
          else next.delete("ask");
          return next;
        },
        { replace: true }
      );
    },
    [setSearchParams]
  );

  const openAssistant = useCallback((question?: string, context?: object) => {
    launchNonce += 1;
    setPending({
      nonce: launchNonce,
      question,
      context: asRecord(context),
      fresh: Boolean(question?.trim())
    });
    setPanel("ask");
    setOpenState(true);
  }, []);

  const openBatch = useCallback(() => {
    setPanel("batch");
    setOpenState(true);
  }, []);

  const toggle = useCallback(() => {
    setOpenState((current) => {
      const next = !current;
      if (next) {
        returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      } else {
        const back = returnFocus.current;
        window.setTimeout(() => back?.focus(), 0);
      }
      return next;
    });
  }, []);

  const value = useMemo(
    () => ({
      open,
      setOpen,
      toggle,
      openAssistant,
      panel,
      setPanel,
      openBatch,
      pending,
      conversationId,
      setConversationId
    }),
    [open, setOpen, toggle, openAssistant, panel, openBatch, pending, conversationId, setConversationId]
  );

  return <AssistantContext.Provider value={value}>{children}</AssistantContext.Provider>;
};

export const useAssistant = (): AssistantContextValue => {
  const ctx = useContext(AssistantContext);
  if (!ctx) throw new Error("useAssistant must be used within AssistantProvider");
  return ctx;
};
