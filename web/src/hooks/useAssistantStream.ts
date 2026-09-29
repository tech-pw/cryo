import { useCallback, useReducer, useRef } from "react";

import {
  listAssistantMessageEvents,
  streamAssistantMessage,
  stopAssistantMessage,
  type AssistantBlock,
  type AssistantEvent,
  type AssistantMessage,
  type AssistantSendBody
} from "../api/assistant";
import type { ApiOptions } from "../api/client";
import { isAbortError } from "../lib/sse";
import { useAuth } from "../lib/auth";

export interface AssistantStep {
  step: number;
  status: "started" | "completed" | "failed";
  label?: string;
  toolName?: string;
  durationMs?: number;
  rowCount?: number;
  evidenceId?: string;
  error?: string;
}

export type AssistantStreamPhase = "idle" | "streaming" | "completed" | "error" | "stopped";

export interface AssistantStreamState {
  phase: AssistantStreamPhase;
  clientMessageId: string | null;
  messageId: string | null;
  conversationId: string | null;
  text: string;
  blocks: AssistantBlock[];
  steps: AssistantStep[];
  clarification: string | null;
  error: string | null;
  proposalIds: string[];
  context: Record<string, unknown> | null;
}

export const initialAssistantStreamState: AssistantStreamState = {
  phase: "idle",
  clientMessageId: null,
  messageId: null,
  conversationId: null,
  text: "",
  blocks: [],
  steps: [],
  clarification: null,
  error: null,
  proposalIds: [],
  context: null
};

const keep = <T>(next: T | undefined, current: T | undefined): T | undefined =>
  next === undefined ? current : next;

const upsertStep = (steps: AssistantStep[], next: AssistantStep): AssistantStep[] => {
  const index = steps.findIndex((step) => step.step === next.step);
  if (index === -1) return [...steps, next];
  const current = steps[index];
  const merged: AssistantStep = {
    ...current,
    ...next,
    label: keep(next.label, current.label),
    toolName: keep(next.toolName, current.toolName),
    evidenceId: keep(next.evidenceId, current.evidenceId),
    error: keep(next.error, current.error)
  };
  const copy = steps.slice();
  copy[index] = merged;
  return copy;
};

const touch = (state: AssistantStreamState): AssistantStreamState =>
  state.phase === "idle" ? { ...state, phase: "streaming" } : state;

export const reduceAssistantEvent = (state: AssistantStreamState, event: AssistantEvent): AssistantStreamState => {
  switch (event.event) {
    case "message.accepted":
      return {
        ...touch(state),
        messageId: event.data.message_id || state.messageId,
        conversationId: event.data.conversation_id ?? state.conversationId
      };
    case "context.resolved":
      return { ...state, context: event.data.context ?? state.context };
    case "clarification.needed":
      return { ...touch(state), clarification: event.data.text };
    case "step.started":
      return {
        ...touch(state),
        steps: upsertStep(state.steps, {
          step: event.data.step,
          status: "started",
          label: event.data.label,
          toolName: event.data.tool_name
        })
      };
    case "step.completed":
      return {
        ...touch(state),
        steps: upsertStep(state.steps, {
          step: event.data.step,
          status: "completed",
          label: event.data.label,
          toolName: event.data.tool_name,
          durationMs: event.data.duration_ms,
          rowCount: event.data.row_count,
          evidenceId: event.data.evidence_id
        })
      };
    case "step.failed":
      return {
        ...touch(state),
        steps: upsertStep(state.steps, {
          step: event.data.step,
          status: "failed",
          label: event.data.label,
          toolName: event.data.tool_name,
          error: event.data.error
        })
      };
    case "answer.delta":
      return { ...touch(state), text: state.text + event.data.text };
    case "answer.block":
      return { ...touch(state), blocks: [...state.blocks, event.data.block] };
    case "proposal.created":
      return {
        ...touch(state),
        proposalIds: event.data.proposal_id ? [...state.proposalIds, event.data.proposal_id] : state.proposalIds,
        blocks: event.data.block ? [...state.blocks, event.data.block] : state.blocks
      };
    case "message.completed":
      return {
        ...state,
        phase: "completed",
        messageId: event.data.message_id ?? state.messageId,
        text: event.data.text ?? state.text,
        blocks: event.data.blocks ?? state.blocks
      };
    case "error":
      return {
        ...state,
        phase: "error",
        error: event.data.message || event.data.detail || "Request failed"
      };
    case "heartbeat":
      return state;
    default:
      return state;
  }
};

export type AssistantStreamAction =
  | AssistantEvent
  | { event: "local.start"; clientMessageId: string }
  | { event: "local.stopped" }
  | { event: "local.finished" };

export const reduceAssistantStream = (
  state: AssistantStreamState,
  action: AssistantStreamAction
): AssistantStreamState => {
  if (action.event === "local.start") {
    return { ...initialAssistantStreamState, phase: "streaming", clientMessageId: action.clientMessageId };
  }
  if (action.event === "local.stopped") return { ...state, phase: "stopped" };
  if (action.event === "local.finished") {
    return state.phase === "streaming" ? { ...state, phase: "completed" } : state;
  }
  return reduceAssistantEvent(state, action);
};

export interface TranscriptMessage {
  id: string;
  serverId: string | null;
  clientMessageId: string | null;
  role: "user" | "assistant";
  text: string;
  blocks: AssistantBlock[];
  steps: AssistantStep[];
  status: "streaming" | "completed" | "error" | "stopped";
  clarification: string | null;
  error: string | null;
  announce: boolean;
}

const statusOf = (status?: string): TranscriptMessage["status"] => {
  if (status === "error" || status === "failed") return "error";
  if (status === "stopped" || status === "cancelled" || status === "canceled") return "stopped";
  if (status === "streaming" || status === "pending") return "streaming";
  if (status === "complete") return "completed";
  return "completed";
};

export const transcriptFromMessage = (message: AssistantMessage): TranscriptMessage => ({
  id: message.id,
  serverId: message.role === "assistant" ? message.id : null,
  clientMessageId: message.client_message_id ?? null,
  role: message.role === "user" ? "user" : "assistant",
  text: message.text ?? "",
  blocks: message.blocks ?? [],
  steps: [],
  status: statusOf(message.status),
  clarification: null,
  error: null,
  announce: false
});

export const applyStream = (message: TranscriptMessage, state: AssistantStreamState): TranscriptMessage => {
  if (!state.clientMessageId || message.clientMessageId !== state.clientMessageId) return message;
  const status: TranscriptMessage["status"] =
    state.phase === "error" || state.phase === "stopped" || state.phase === "completed" || state.phase === "streaming"
      ? state.phase
      : message.status;
  return {
    ...message,
    serverId: state.messageId ?? message.serverId,
    text: state.text,
    blocks: state.blocks,
    steps: state.steps,
    status,
    clarification: state.clarification,
    error: state.error,
    announce: state.phase === "completed"
  };
};

export interface AssistantSendInput {
  conversationId: string;
  clientMessageId: string;
  body: AssistantSendBody;
}

export const useAssistantStream = () => {
  const { api } = useAuth();
  const [state, dispatch] = useReducer(reduceAssistantStream, initialAssistantStreamState);
  const abortRef = useRef<AbortController | null>(null);
  const messageIdRef = useRef<string | null>(null);
  const seqRef = useRef(0);
  const apiRef = useRef<ApiOptions>(api);
  apiRef.current = api;

  const send = useCallback(async (input: AssistantSendInput) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    messageIdRef.current = null;
    seqRef.current = 0;
    dispatch({ event: "local.start", clientMessageId: input.clientMessageId });
    try {
      await streamAssistantMessage(
        apiRef.current,
        input.conversationId,
        input.body,
        (event, seq) => {
          if (seq !== undefined && Number.isFinite(seq)) seqRef.current = seq;
          if (event.event === "message.accepted" && event.data.message_id) {
            messageIdRef.current = event.data.message_id;
          }
          dispatch(event);
        },
        controller.signal
      );
      dispatch({ event: "local.finished" });
    } catch (error) {
      if (controller.signal.aborted || isAbortError(error)) {
        dispatch({ event: "local.stopped" });
        return;
      }
      const messageId = messageIdRef.current;
      if (messageId) {
        try {
          const resumed = await listAssistantMessageEvents(apiRef.current, messageId, seqRef.current);
          for (const event of resumed) dispatch(event);
          if (resumed.some((event) => event.event === "message.completed" || event.event === "error")) {
            dispatch({ event: "local.finished" });
            return;
          }
        } catch {
          // The live stream failed and replay was unavailable.
        }
      }
      dispatch({
        event: "error",
        data: { message: error instanceof Error ? error.message : "Request failed" }
      });
      throw error;
    }
  }, []);

  const stop = useCallback(async () => {
    abortRef.current?.abort();
    const messageId = messageIdRef.current;
    if (messageId) {
      try {
        await stopAssistantMessage(apiRef.current, messageId);
      } catch {
        dispatch({ event: "local.stopped" });
        return;
      }
    }
    dispatch({ event: "local.stopped" });
  }, []);

  return { state, send, stop };
};
