import { API_PREFIX, ApiError, apiFetch, type ApiOptions } from "./client";
import { formatNumber } from "../lib/format";
import { consumeSse, type SseFrame } from "../lib/sse";

export interface AssistantCapabilities {
  enabled: boolean;
  provider_configured: boolean;
  supports_tools: boolean;
  tiers_enabled: number[];
  writes_enabled: boolean;
  role: string;
  reasons: string[];
}

export interface AssistantStarter {
  text: string;
  verified_handler?: string | null;
}

export interface AssistantMention {
  kind: string;
  id: string;
}

export interface AssistantColumn {
  key: string;
  header?: string;
  label?: string;
  align?: "left" | "right";
}

export interface AssistantChartItem {
  id?: string;
  label?: string;
  name?: string;
  value?: number | null;
}

export interface AssistantChartJob {
  id: string;
  table?: string;
  engine?: string;
  status?: string;
  startedAt?: string | null;
  finishedAt?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
}

export type AssistantChartKind =
  | "ranked_bars"
  | "time_series"
  | "before_after"
  | "distribution"
  | "stacked_share"
  | "run_lanes"
  | string;

export type AssistantBlock =
  | {
      type: "answer";
      text: string;
      verified?: boolean;
      verified_handler?: string | null;
      evidence_ids?: string[];
    }
  | {
      type: "ranked_table";
      title?: string;
      columns?: AssistantColumn[];
      rows?: Array<Record<string, unknown>>;
      evidence_ids?: string[];
    }
  | {
      type: "comparison";
      title?: string;
      columns?: AssistantColumn[];
      rows?: Array<Record<string, unknown>>;
      left?: Record<string, unknown>;
      right?: Record<string, unknown>;
      unit?: string;
      comparable?: boolean;
      note?: string;
      evidence_ids?: string[];
    }
  | {
      type: "chart";
      kind?: AssistantChartKind;
      title?: string;
      unit?: string;
      items?: AssistantChartItem[];
      jobs?: AssistantChartJob[];
      series?: Array<Record<string, unknown>>;
      table?: { columns?: AssistantColumn[]; rows?: Array<Record<string, unknown>> };
      columns?: AssistantColumn[];
      rows?: Array<Record<string, unknown>>;
      evidence_ids?: string[];
    }
  | {
      type: "investigation";
      title?: string;
      summary?: string;
      symptom?: string;
      findings?: Array<{ label?: string; detail?: string }>;
      hypotheses?: Array<Record<string, unknown>>;
      missing_inputs?: string[];
      next_step?: string;
      evidence_ids?: string[];
    }
  | {
      type: "action_review";
      proposal_id: string;
      title?: string;
      action_type?: string;
      tier?: number;
      preview?: Record<string, unknown> | null;
      required_role?: string;
      status?: string;
      expires_at?: string;
    }
  | {
      type: "execution";
      proposal_id: string;
      status?: string;
      run_id?: string | null;
    }
  | {
      type: "outcome";
      proposal_id?: string;
      status?: string;
      summary?: string;
      run_id?: string | null;
      requested?: number | null;
      completed?: number | null;
      failed?: number | null;
      skipped?: number | null;
    }
  | {
      type: "gap";
      text?: string;
      reason?: string;
      missing_input?: string;
      setup_url?: string | null;
      evidence_ids?: string[];
    }
  | {
      type: "verified";
      handler?: string | null;
      text?: string;
      evidence_ids?: string[];
    };

export type AssistantEvent =
  | {
      event: "message.accepted";
      data: { message_id: string; conversation_id?: string; client_message_id?: string };
    }
  | { event: "context.resolved"; data: { context?: Record<string, unknown> } }
  | { event: "clarification.needed"; data: { text: string; message_id?: string } }
  | {
      event: "step.started";
      data: { step: number; label?: string; tool_name?: string; message_id?: string };
    }
  | {
      event: "step.completed";
      data: {
        step: number;
        label?: string;
        tool_name?: string;
        duration_ms?: number;
        row_count?: number;
        evidence_id?: string;
        message_id?: string;
      };
    }
  | {
      event: "step.failed";
      data: { step: number; label?: string; tool_name?: string; error?: string; message_id?: string };
    }
  | { event: "answer.delta"; data: { text: string; message_id?: string } }
  | { event: "answer.block"; data: { block: AssistantBlock; message_id?: string } }
  | {
      event: "proposal.created";
      data: { proposal_id: string; block?: AssistantBlock; message_id?: string };
    }
  | {
      event: "message.completed";
      data: { message_id?: string; status?: string; text?: string; blocks?: AssistantBlock[] };
    }
  | { event: "error"; data: { message?: string; detail?: string; code?: string; message_id?: string } }
  | { event: "heartbeat"; data: { ts?: string } };

export interface AssistantMessage {
  id: string;
  conversation_id?: string;
  seq?: number;
  role: string;
  text?: string | null;
  blocks?: AssistantBlock[] | null;
  status?: string;
  verified_handler?: string | null;
  grounding?: string | null;
  client_message_id?: string | null;
  created_at?: string;
  completed_at?: string | null;
}

export interface AssistantConversation {
  id: string;
  title?: string | null;
  context?: Record<string, unknown>;
  status?: string;
  created_at?: string;
  updated_at?: string;
  messages?: AssistantMessage[];
  share_url?: string | null;
}

export interface AssistantConversationPage {
  items: AssistantConversation[];
  next_cursor: string | null;
}

export interface AssistantProposal {
  id: string;
  conversation_id?: string | null;
  message_id?: string | null;
  action_type: string;
  tier: number;
  arguments?: Record<string, unknown>;
  preview?: Record<string, unknown> | null;
  plan_hash?: string | null;
  required_role: string;
  status: string;
  expires_at?: string;
  run_id?: string | null;
  job_ids?: string[] | null;
  superseded_by?: string | null;
  last_error?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface AssistantEvidence {
  id: string;
  classification?: string;
  observed_at?: string | null;
  retrieved_at?: string;
  source_refs?: unknown;
  coverage?: Record<string, unknown> | null;
  result?: Record<string, unknown> | null;
}

export interface AssistantSettings {
  enabled: boolean;
  allowed_min_role: string;
  enabled_tiers: number[];
  send_row_values: boolean;
  retention_days?: number;
  team_instructions_skillset?: string | null;
  [key: string]: unknown;
}

export interface AssistantUsageRow {
  id?: string;
  subject?: string | null;
  day?: string | null;
  turns?: number | null;
  tokens?: number | null;
  tool_calls?: number | null;
  [key: string]: unknown;
}

export interface AssistantApiKey {
  id: string;
  name: string;
  scopes?: string[];
  created_by?: string;
  created_at?: string;
  last_used_at?: string | null;
  revoked_at?: string | null;
  token?: string;
  key?: string;
  secret?: string;
}

export interface AssistantShare {
  token?: string;
  url?: string;
  share_url?: string;
}

export interface AssistantSendBody {
  text: string;
  context: Record<string, unknown>;
  mentions: AssistantMention[];
  client_message_id: string;
}

export class ProposalConflictError extends Error {
  readonly revisedProposalId: string | null;

  constructor(revisedProposalId: string | null) {
    super("The plan changed");
    this.name = "ProposalConflictError";
    this.revisedProposalId = revisedProposalId;
  }
}

const EVENT_NAMES = new Set<AssistantEvent["event"]>([
  "message.accepted",
  "context.resolved",
  "clarification.needed",
  "step.started",
  "step.completed",
  "step.failed",
  "answer.delta",
  "answer.block",
  "proposal.created",
  "message.completed",
  "error",
  "heartbeat"
]);

const BLOCK_TYPES = new Set([
  "answer",
  "ranked_table",
  "comparison",
  "chart",
  "investigation",
  "action_review",
  "execution",
  "outcome",
  "gap",
  "verified"
]);

export const TERMINAL_PROPOSAL_STATUSES = new Set([
  "succeeded",
  "failed",
  "rejected",
  "expired",
  "cancelled",
  "canceled",
  "superseded",
  "completed",
  "complete",
  "dispatch_uncertain"
]);

export const isTerminalProposalStatus = (status?: string | null): boolean =>
  Boolean(status && TERMINAL_PROPOSAL_STATUSES.has(status.toLowerCase()));

export const formatAssistantCell = (value: unknown): string => {
  if (typeof value === "number") return formatNumber(value);
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "string") return value;
  return "-";
};

export const mentionsFromContext = (context: Record<string, unknown>): AssistantMention[] => {
  const mentions: AssistantMention[] = [];
  if (typeof context.table_id === "string" && context.table_id) {
    mentions.push({ kind: "table", id: context.table_id });
  }
  if (typeof context.run_id === "string" && context.run_id) {
    mentions.push({ kind: "run", id: context.run_id });
  }
  return mentions;
};

export const assistantErrorMessage = (error: unknown): string => {
  if (error instanceof ProposalConflictError) return error.message;
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error && error.message) return error.message;
  return "Request failed";
};

const asRecord = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};

const optionalString = (record: Record<string, unknown>, key: string): string | undefined =>
  typeof record[key] === "string" ? record[key] : undefined;

const numberField = (record: Record<string, unknown>, key: string): number | undefined =>
  typeof record[key] === "number" && Number.isFinite(record[key]) ? (record[key] as number) : undefined;

const readBlock = (value: unknown): AssistantBlock | null => {
  const record = asRecord(value);
  const type = record.type;
  if (typeof type !== "string" || !BLOCK_TYPES.has(type)) return null;
  return record as unknown as AssistantBlock;
};

const unwrapEvent = (
  parsed: unknown
): { messageId?: string; payload: Record<string, unknown> } => {
  const record = asRecord(parsed);
  if ("payload" in record) {
    return { messageId: optionalString(record, "message_id"), payload: asRecord(record.payload) };
  }
  return { messageId: optionalString(record, "message_id"), payload: record };
};

export const frameToAssistantEvent = (frame: SseFrame): AssistantEvent | null => {
  if (!EVENT_NAMES.has(frame.event as AssistantEvent["event"])) return null;
  let parsed: unknown = {};
  if (frame.data) {
    try {
      parsed = JSON.parse(frame.data) as unknown;
    } catch {
      parsed = { message: frame.data, text: frame.data };
    }
  }
  const { messageId, payload } = unwrapEvent(parsed);
  const data = payload;
  switch (frame.event) {
    case "message.accepted":
      return {
        event: "message.accepted",
        data: {
          message_id: optionalString(data, "message_id") ?? messageId ?? "",
          conversation_id: optionalString(data, "conversation_id"),
          client_message_id: optionalString(data, "client_message_id")
        }
      };
    case "context.resolved":
      return { event: "context.resolved", data: { context: asRecord(data.context ?? data) } };
    case "clarification.needed":
      return {
        event: "clarification.needed",
        data: {
          text: optionalString(data, "text") ?? optionalString(data, "question") ?? optionalString(data, "message") ?? "",
          message_id: optionalString(data, "message_id") ?? messageId
        }
      };
    case "step.started":
      return {
        event: "step.started",
        data: {
          step: numberField(data, "step") ?? 0,
          label: optionalString(data, "label"),
          tool_name: optionalString(data, "tool_name"),
          message_id: optionalString(data, "message_id")
        }
      };
    case "step.completed":
      return {
        event: "step.completed",
        data: {
          step: numberField(data, "step") ?? 0,
          label: optionalString(data, "label"),
          tool_name: optionalString(data, "tool_name") ?? optionalString(data, "tool"),
          duration_ms: numberField(data, "duration_ms"),
          row_count: numberField(data, "row_count"),
          evidence_id: optionalString(data, "evidence_id"),
          message_id: optionalString(data, "message_id") ?? messageId
        }
      };
    case "step.failed":
      return {
        event: "step.failed",
        data: {
          step: numberField(data, "step") ?? 0,
          label: optionalString(data, "label"),
          tool_name: optionalString(data, "tool_name"),
          error: optionalString(data, "error") ?? optionalString(data, "message"),
          message_id: optionalString(data, "message_id")
        }
      };
    case "answer.delta":
      return {
        event: "answer.delta",
        data: { text: optionalString(data, "text") ?? "", message_id: optionalString(data, "message_id") }
      };
    case "answer.block": {
      const block = readBlock(data.block ?? data);
      if (!block) return null;
      return { event: "answer.block", data: { block, message_id: optionalString(data, "message_id") } };
    }
    case "proposal.created": {
      const proposal = asRecord(data.proposal);
      const proposalId =
        optionalString(data, "proposal_id") ?? optionalString(proposal, "id") ?? optionalString(proposal, "proposal_id") ?? "";
      const block = readBlock(data.block) ?? ({ type: "action_review", proposal_id: proposalId } as AssistantBlock);
      return { event: "proposal.created", data: { proposal_id: proposalId, block, message_id: optionalString(data, "message_id") } };
    }
    case "message.completed":
      return {
        event: "message.completed",
        data: {
          message_id: optionalString(data, "message_id") ?? messageId,
          status: optionalString(data, "status"),
          text: optionalString(data, "text"),
          blocks: Array.isArray(data.blocks)
            ? data.blocks.map(readBlock).filter((block): block is AssistantBlock => block !== null)
            : undefined
        }
      };
    case "error":
      return {
        event: "error",
        data: {
          message: optionalString(data, "message") ?? optionalString(data, "detail"),
          detail: optionalString(data, "detail"),
          code: optionalString(data, "code"),
          message_id: optionalString(data, "message_id")
        }
      };
    case "heartbeat":
      return { event: "heartbeat", data: { ts: optionalString(data, "ts") } };
    default:
      return null;
  }
};

export const revisedProposalId = (detail: unknown): string | null => {
  if (typeof detail === "string") {
    const match = detail.match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i);
    return match?.[0] ?? null;
  }
  const record = asRecord(detail);
  for (const key of ["revised_proposal_id", "proposal_id", "superseded_by", "id"]) {
    const value = record[key];
    if (typeof value === "string" && value) return value;
  }
  if (record.detail !== undefined) return revisedProposalId(record.detail);
  return null;
};

const asPage = (payload: unknown): AssistantConversationPage => {
  if (Array.isArray(payload)) return { items: payload as AssistantConversation[], next_cursor: null };
  const record = asRecord(payload);
  const items = Array.isArray(record.items)
    ? (record.items as AssistantConversation[])
    : Array.isArray(record.conversations)
      ? (record.conversations as AssistantConversation[])
      : [];
  return { items, next_cursor: typeof record.next_cursor === "string" ? record.next_cursor : null };
};

export const usageRows = (payload: unknown): AssistantUsageRow[] => {
  if (Array.isArray(payload)) return payload as AssistantUsageRow[];
  const record = asRecord(payload);
  if (Array.isArray(record.items)) return record.items as AssistantUsageRow[];
  if (Array.isArray(record.rows)) return record.rows as AssistantUsageRow[];
  if (Object.keys(record).length === 0) return [];
  return [record as AssistantUsageRow];
};

export const revealedApiKey = (key: AssistantApiKey): string | null => key.token ?? key.key ?? key.secret ?? null;

const apiPut = <T>(path: string, api: ApiOptions, body: unknown): Promise<T> =>
  apiFetch<T>(path, api, { method: "PUT" as "POST", body });

export const getAssistantCapabilities = (api: ApiOptions) =>
  apiFetch<AssistantCapabilities>("/assistant/capabilities", api);

export const getAssistantStarters = (
  api: ApiOptions,
  query: { route?: string; table_id?: string; run_id?: string }
) => apiFetch<AssistantStarter[]>("/assistant/starters", api, { query });

export const createAssistantConversation = (
  api: ApiOptions,
  body: { title?: string; context: Record<string, unknown> }
) => apiFetch<AssistantConversation>("/assistant/conversations", api, { method: "POST", body });

export const listAssistantConversations = async (
  api: ApiOptions,
  query: { cursor?: string; limit?: number } = {}
): Promise<AssistantConversationPage> => asPage(await apiFetch<unknown>("/assistant/conversations", api, { query }));

export const getAssistantConversation = (api: ApiOptions, id: string) =>
  apiFetch<AssistantConversation>(`/assistant/conversations/${id}`, api);

export const patchAssistantConversation = (
  api: ApiOptions,
  id: string,
  body: { title?: string; status?: string }
) => apiFetch<AssistantConversation>(`/assistant/conversations/${id}`, api, { method: "PATCH", body });

export const deleteAssistantConversation = (api: ApiOptions, id: string) =>
  apiFetch<void>(`/assistant/conversations/${id}`, api, { method: "DELETE" });

export const shareAssistantConversation = (api: ApiOptions, id: string) =>
  apiFetch<AssistantShare>(`/assistant/conversations/${id}/share`, api, { method: "POST" });

export const unshareAssistantConversation = (api: ApiOptions, id: string) =>
  apiFetch<void>(`/assistant/conversations/${id}/share`, api, { method: "DELETE" });

export const stopAssistantMessage = (api: ApiOptions, id: string) =>
  apiFetch<void>(`/assistant/messages/${id}/stop`, api, { method: "POST" });

export const listAssistantMessageEvents = async (
  api: ApiOptions,
  id: string,
  afterSeq?: number,
  signal?: AbortSignal
): Promise<AssistantEvent[]> => {
  const url = new URL(`${API_PREFIX}/assistant/messages/${id}/events`, window.location.origin);
  if (afterSeq !== undefined) url.searchParams.set("after_seq", String(afterSeq));
  const response = await fetch(`${url.pathname}${url.search}`, {
    headers: {
      Authorization: `Bearer ${api.token}`,
      Accept: "text/event-stream",
      "X-Correlation-ID": crypto.randomUUID()
    },
    signal
  });
  if (!response.ok) {
    const payload = await readErrorPayload(response);
    const record = asRecord(payload);
    const detail = record.detail ?? payload;
    throw new ApiError(response.status, String(detail || response.statusText), detail, response.headers.get("x-correlation-id"));
  }
  if (!response.body) return [];
  const events: AssistantEvent[] = [];
  await consumeSse(response.body, (frame) => {
    const event = frameToAssistantEvent(frame);
    if (event) events.push(event);
  }, signal);
  return events;
};

export const sendAssistantFeedback = (
  api: ApiOptions,
  id: string,
  body: { rating: number; reason?: string }
) => apiFetch<void>(`/assistant/messages/${id}/feedback`, api, { method: "POST", body });

export const getAssistantEvidence = (api: ApiOptions, id: string) =>
  apiFetch<AssistantEvidence>(`/assistant/evidence/${id}`, api);

export const getAssistantProposal = (api: ApiOptions, id: string) =>
  apiFetch<AssistantProposal>(`/assistant/proposals/${id}`, api);

export const authorizeAssistantProposal = async (
  api: ApiOptions,
  id: string,
  body: { note?: string; idempotency_key: string; channel: "ui_card" }
): Promise<AssistantProposal> => {
  try {
    return await apiFetch<AssistantProposal>(`/assistant/proposals/${id}/authorize`, api, { method: "POST", body });
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      throw new ProposalConflictError(revisedProposalId(error.detail));
    }
    throw error;
  }
};

export const rejectAssistantProposal = (api: ApiOptions, id: string) =>
  apiFetch<AssistantProposal>(`/assistant/proposals/${id}/reject`, api, { method: "POST" });

export const getAssistantSettings = (api: ApiOptions) => apiFetch<AssistantSettings>("/assistant/settings", api);

export const putAssistantSettings = (api: ApiOptions, body: AssistantSettings) =>
  apiPut<AssistantSettings>("/assistant/settings", api, body);

export const getAssistantUsage = (api: ApiOptions) => apiFetch<unknown>("/assistant/usage", api);

export const listAssistantApiKeys = async (api: ApiOptions): Promise<AssistantApiKey[]> => {
  const payload = await apiFetch<unknown>("/assistant/api-keys", api);
  if (Array.isArray(payload)) return payload as AssistantApiKey[];
  const record = asRecord(payload);
  if (Array.isArray(record.items)) return record.items as AssistantApiKey[];
  return [];
};

export const createAssistantApiKey = (api: ApiOptions, body: { name: string; scopes?: string[] }) =>
  apiFetch<AssistantApiKey>("/assistant/api-keys", api, { method: "POST", body });

export const deleteAssistantApiKey = (api: ApiOptions, id: string) =>
  apiFetch<void>(`/assistant/api-keys/${id}`, api, { method: "DELETE" });

const readErrorPayload = async (response: Response): Promise<unknown> => {
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) return response.json();
  return response.text();
};

export const streamAssistantMessage = async (
  api: ApiOptions,
  conversationId: string,
  body: AssistantSendBody,
  onEvent: (event: AssistantEvent, seq?: number) => void,
  signal: AbortSignal
): Promise<void> => {
  const url = new URL(
    `${API_PREFIX}/assistant/conversations/${conversationId}/messages`,
    window.location.origin
  );
  const response = await fetch(`${url.pathname}${url.search}`, {
    method: "POST",
    headers: {
      "X-Correlation-ID": crypto.randomUUID(),
      Authorization: `Bearer ${api.token}`,
      "Content-Type": "application/json",
      Accept: "text/event-stream"
    },
    body: JSON.stringify(body),
    signal
  });

  if (!response.ok) {
    const payload = await readErrorPayload(response);
    const record = asRecord(payload);
    const detail = record.detail ?? payload;
    throw new ApiError(response.status, String(detail || response.statusText), detail, response.headers.get("x-correlation-id"));
  }
  if (!response.body) throw new Error("Empty assistant stream");
  await consumeSse(
    response.body,
    (frame) => {
      const event = frameToAssistantEvent(frame);
      if (event) onEvent(event, frame.id ? Number(frame.id) : undefined);
    },
    signal
  );
};
