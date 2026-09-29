import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import {
  assistantErrorMessage,
  createAssistantConversation,
  getAssistantCapabilities,
  getAssistantConversation,
  getAssistantStarters,
  mentionsFromContext,
  type AssistantStarter
} from "../../api/assistant";
import { useAssistantStream, applyStream, transcriptFromMessage, type TranscriptMessage } from "../../hooks/useAssistantStream";
import { usePageContext } from "../../hooks/usePageContext";
import { useAuth } from "../../lib/auth";
import { BatchPanel } from "../batch/BatchPanel";
import { cn } from "../../lib/cn";
import { useReducedMotion } from "../../lib/motion";
import { isAbortError } from "../../lib/sse";
import { Alert } from "../ui/alert";
import { Sheet, SheetContent } from "../ui/sheet";
import { useAssistant } from "./AssistantProvider";
import { ConversationHistory } from "./ConversationHistory";
import { ConversationView } from "./ConversationView";
import { DrawerHeader } from "./DrawerHeader";

const WIDTH_KEY = "cryo.askDrawerWidth";
const DEFAULT_WIDTH = 440;
const handledLaunches = new Set<number>();

const readWidth = (): number => {
  try {
    const raw = window.localStorage.getItem(WIDTH_KEY);
    const value = raw == null ? DEFAULT_WIDTH : Number(raw);
    if (!Number.isFinite(value)) return DEFAULT_WIDTH;
    return Math.min(960, Math.max(360, value));
  } catch {
    return DEFAULT_WIDTH;
  }
};

const writeWidth = (width: number): void => {
  try {
    window.localStorage.setItem(WIDTH_KEY, String(Math.round(width)));
  } catch {
    return;
  }
};

export const AskCryoDrawer = () => {
  const { open, setOpen, pending, conversationId, setConversationId, panel, setPanel } = useAssistant();
  const { subject } = useAuth();
  const { api, token } = useAuth();
  const pageContext = usePageContext();
  const reduced = useReducedMotion();
  const stream = useAssistantStream();
  const [width, setWidth] = useState(readWidth);
  const [expanded, setExpanded] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [messages, setMessages] = useState<TranscriptMessage[]>([]);
  const [extraContext, setExtraContext] = useState<Record<string, unknown>>({});
  const [removed, setRemoved] = useState<string[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const sendingRef = useRef(false);
  const previousWidth = useRef(width);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const seenAsk = useRef<string | null>(null);

  const context = useMemo(() => {
    const merged: Record<string, unknown> = { ...pageContext, ...extraContext };
    for (const key of removed) delete merged[key];
    return merged;
  }, [pageContext, extraContext, removed]);

  const capabilities = useQuery({
    queryKey: ["assistant", "capabilities", token],
    queryFn: () => getAssistantCapabilities(api),
    staleTime: 60_000,
    retry: false
  });
  const starters = useQuery({
    queryKey: ["assistant", "starters", context.route, context.table_id, context.run_id, token],
    queryFn: () =>
      getAssistantStarters(api, {
        route: typeof context.route === "string" ? context.route : undefined,
        table_id: typeof context.table_id === "string" ? context.table_id : undefined,
        run_id: typeof context.run_id === "string" ? context.run_id : undefined
      }),
    enabled: open,
    retry: false
  });

  const submitRef = useRef<
    (text: string, extra?: Record<string, unknown>, fresh?: boolean, starter?: AssistantStarter) => Promise<void>
  >(async () => undefined);

  submitRef.current = async (text, extra, fresh = false, starter) => {
    const trimmed = text.trim();
    if (!trimmed || sendingRef.current) return;
    sendingRef.current = true;
    setLoadError(null);
    const clientMessageId = crypto.randomUUID();
    const merged: Record<string, unknown> = { ...pageContext, ...extraContext, ...extra };
    for (const key of removed) {
      if (!extra || !(key in extra)) delete merged[key];
    }
    if (starter?.verified_handler) merged.verified_handler = starter.verified_handler;
    if (fresh) setConversationId(null);
    const user: TranscriptMessage = {
      id: `user-${clientMessageId}`,
      serverId: null,
      clientMessageId: null,
      role: "user",
      text: trimmed,
      blocks: [],
      steps: [],
      status: "completed",
      clarification: null,
      error: null,
      announce: false
    };
    const assistant: TranscriptMessage = {
      id: clientMessageId,
      serverId: null,
      clientMessageId,
      role: "assistant",
      text: "",
      blocks: [],
      steps: [],
      status: "streaming",
      clarification: null,
      error: null,
      announce: false
    };
    setMessages((current) => (fresh ? [user, assistant] : [...current, user, assistant]));
    setDraft("");
    setHistoryOpen(false);
    try {
      let id = fresh ? null : conversationId;
      if (!id) {
        const created = await createAssistantConversation(api, { title: trimmed.slice(0, 80), context: merged });
        id = created.id;
        setConversationId(created.id);
      }
      await stream.send({
        conversationId: id,
        clientMessageId,
        body: {
          text: trimmed,
          context: merged,
          mentions: mentionsFromContext(merged),
          client_message_id: clientMessageId
        }
      });
    } catch (error) {
      if (!isAbortError(error)) {
        setMessages((current) =>
          current.map((message) =>
            message.clientMessageId === clientMessageId
              ? { ...message, status: "error", error: assistantErrorMessage(error) }
              : message
          )
        );
      }
    } finally {
      sendingRef.current = false;
    }
  };

  useEffect(() => {
    setMessages((current) => current.map((message) => applyStream(message, stream.state)));
  }, [stream.state]);

  useEffect(() => {
    if (!conversationId || seenAsk.current === conversationId) return;
    seenAsk.current = conversationId;
    setOpen(true);
  }, [conversationId, setOpen]);

  useEffect(() => {
    if (!conversationId || sendingRef.current) return;
    let cancelled = false;
    void getAssistantConversation(api, conversationId)
      .then((conversation) => {
        if (cancelled || sendingRef.current) return;
        setMessages((conversation.messages ?? []).map(transcriptFromMessage));
        if (conversation.context) setExtraContext(conversation.context);
        setLoadError(null);
      })
      .catch((error) => {
        if (!cancelled) setLoadError(assistantErrorMessage(error));
      });
    return () => {
      cancelled = true;
    };
  }, [api, conversationId]);

  useEffect(() => {
    if (!pending || handledLaunches.has(pending.nonce)) return;
    handledLaunches.add(pending.nonce);
    if (pending.context) {
      setExtraContext(pending.context);
      setRemoved([]);
    }
    if (pending.question?.trim()) {
      void submitRef.current(pending.question, pending.context, pending.fresh);
    }
  }, [pending]);

  useEffect(() => {
    if (open) composerRef.current?.focus();
  }, [open]);

  const handleOpenChange = (next: boolean) => {
    setOpen(next);
  };

  const toggleExpanded = () => {
    if (expanded) {
      setWidth(previousWidth.current);
      writeWidth(previousWidth.current);
      setExpanded(false);
      return;
    }
    previousWidth.current = width;
    const next = Math.min(960, Math.max(640, window.innerWidth - 48));
    setWidth(next);
    writeWidth(next);
    setExpanded(true);
  };

  const onResizeDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = width;
    const move = (ev: PointerEvent) => {
      const next = Math.min(960, Math.max(360, startWidth + (startX - ev.clientX)));
      setWidth(next);
      setExpanded(false);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      setWidth((current) => {
        writeWidth(current);
        return current;
      });
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  return (
    <Sheet open={open} onOpenChange={handleOpenChange}>
      <SheetContent
        side="right"
        className={cn(
          "flex h-full max-w-none flex-col gap-0 overflow-hidden p-0 sm:max-w-none",
          reduced && "animate-none duration-0"
        )}
        style={{ width, maxWidth: width, animation: reduced ? "none" : undefined }}
      >
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize Ask Cryo"
          className="absolute inset-y-0 left-0 z-10 w-1.5 cursor-col-resize"
          onPointerDown={onResizeDown}
        />
        <div className="flex gap-2 border-b px-4 py-2">
          <button type="button" className="text-sm" aria-pressed={panel === "batch"} onClick={() => setPanel("batch")}>
            Batch
          </button>
          <button type="button" className="text-sm" aria-pressed={panel === "ask"} onClick={() => setPanel("ask")}>
            Ask Cryo
          </button>
        </div>
        {panel === "batch" ? (
          <div className="min-h-0 flex-1 overflow-y-auto p-4">
            <BatchPanel
              identity={{
                installationId: "local",
                principalId: subject || "anonymous",
                tenantId: "local"
              }}
            />
          </div>
        ) : (
        <>
        <DrawerHeader
          expanded={expanded}
          historyOpen={historyOpen}
          onExpand={toggleExpanded}
          onHistory={() => setHistoryOpen((current) => !current)}
          onNew={() => {
            void stream.stop();
            sendingRef.current = false;
            setConversationId(null);
            setMessages([]);
            setExtraContext({});
            setRemoved([]);
            setDraft("");
            setHistoryOpen(false);
            setLoadError(null);
          }}
        />
        <div className="flex min-h-0 flex-1 flex-col">
          {loadError ? (
            <div className="px-4 pt-3">
              <Alert variant="error" title="Ask Cryo" description={loadError} />
            </div>
          ) : null}
          {historyOpen ? (
            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
              <ConversationHistory
                activeId={conversationId}
                onSelect={(id) => {
                  sendingRef.current = false;
                  setConversationId(id);
                  setHistoryOpen(false);
                  setMessages([]);
                }}
              />
            </div>
          ) : (
            <ConversationView
              messages={messages}
              starters={starters.data ?? []}
              capabilities={capabilities.data}
              context={context}
              draft={draft}
              streaming={stream.state.phase === "streaming"}
              composerRef={composerRef}
              onDraft={setDraft}
              onSend={() => void submitRef.current(draft)}
              onStop={() => void stream.stop()}
              onStarter={(starter) => void submitRef.current(starter.text, undefined, false, starter)}
              onRemoveContext={(key) => setRemoved((current) => (current.includes(key) ? current : [...current, key]))}
            />
          )}
        </div>
        </>
        )}
      </SheetContent>
    </Sheet>
  );
};
