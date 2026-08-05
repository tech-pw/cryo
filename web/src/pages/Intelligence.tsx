import { useMutation, useQuery } from "@tanstack/react-query";
import { Bot, RotateCcw, Wrench, Zap } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import {
  aiChat,
  askCopilotNatural,
  askCopilotQuestion,
  callMcpTool,
  getBanditStatus,
  getCopilotQuestions,
  getCostModelEvaluate,
  getQPPConfidence,
  listMcpTools,
  resetBandit,
  searchCopilot
} from "../api/client";
import { AsyncBoundary } from "../components/AsyncBoundary";
import { ConfirmAction } from "../components/confirm";
import { EmptyState, PageHeader, TelemetryRequiredNotice } from "../components/common";
import { Alert } from "../components/ui/alert";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Textarea } from "../components/ui/textarea";
import { useAuth } from "../lib/auth";
import { formatNumber } from "../lib/format";

const JsonBlock = ({ value, title }: { value: string; title?: string }) => (
  <Card>
    {title ? (
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">{title}</CardTitle>
      </CardHeader>
    ) : null}
    <CardContent>
      <pre className="inline-json m-0 whitespace-pre-wrap text-xs">{value}</pre>
    </CardContent>
  </Card>
);

const StatPair = ({ label, value }: { label: string; value: ReactNode }) => (
  <div>
    <p className="text-xs text-muted-foreground">{label}</p>
    <p className="text-lg font-semibold [font-variant-numeric:tabular-nums]">{value}</p>
  </div>
);

const DescriptionList = ({ items }: { items: Array<{ label: string; value: ReactNode }> }) => (
  <dl className="space-y-2 text-sm">
    {items.map((item) => (
      <div key={item.label} className="flex justify-between gap-4">
        <dt className="text-muted-foreground">{item.label}</dt>
        <dd className="font-medium text-right">{item.value}</dd>
      </div>
    ))}
  </dl>
);

/** Human-readable source labels for the copilot's known, backend-reported `source` values only
 * (`verified` / `embedding_fallback` / `none` from `answer_question` in `cryo/ai/copilot.py`).
 * Unknown/future values fall back to the raw string rather than inventing a label. */
const SOURCE_LABELS: Record<string, string> = {
  verified: "Verified playbook",
  embedding_fallback: "Embedding similarity match",
  none: "No match found"
};

/** Distinguishes deterministic lookups (verified playbook / embedding retrieval — no LLM
 * involved) from LLM-generated answers, using only fields the backend actually returns. */
type AnswerMeta =
  | { kind: "deterministic"; source: string; questionId?: string | null; similarity?: number | null }
  | { kind: "generated"; provider: string; model: string; tokens?: number };

const AnswerMetaRow = ({ meta, respondedAt }: { meta: AnswerMeta; respondedAt: Date | null }) => (
  <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
    <Badge variant={meta.kind === "deterministic" ? "secondary" : "outline"}>
      {meta.kind === "deterministic" ? "Deterministic" : "Generated"}
    </Badge>
    <span>
      {meta.kind === "deterministic"
        ? (SOURCE_LABELS[meta.source] ?? meta.source)
        : `${meta.provider} · ${meta.model}${meta.tokens != null ? ` · ${formatNumber(meta.tokens)} tokens` : ""}`}
    </span>
    {meta.kind === "deterministic" && meta.similarity != null ? (
      <span>· similarity {(meta.similarity * 100).toFixed(0)}%</span>
    ) : null}
    {meta.kind === "deterministic" && meta.questionId ? <span>· {meta.questionId}</span> : null}
    {respondedAt ? <span>· answered {respondedAt.toLocaleTimeString()}</span> : null}
  </div>
);

/** Shared "structured field + advanced JSON" pattern: `table_id` is the one context/argument key
 * the app already documents via its own placeholders (`{"table_id": "..."}`) across both the
 * chat-context and MCP-arguments inputs, so it is safe to promote to a real labelled control.
 * No other keys are invented — everything else stays inside the advanced JSON editor. */
interface StructuredJsonEditorProps {
  idPrefix: string;
  tableId: string;
  onTableIdChange: (value: string) => void;
  advanced: boolean;
  onAdvancedChange: (value: boolean) => void;
  json: string;
  onJsonChange: (value: string) => void;
  jsonError?: string | null;
  jsonPlaceholder: string;
  label: string;
}

const StructuredJsonEditor = ({
  idPrefix,
  tableId,
  onTableIdChange,
  advanced,
  onAdvancedChange,
  json,
  onJsonChange,
  jsonError,
  jsonPlaceholder,
  label
}: StructuredJsonEditorProps) => {
  const jsonId = `${idPrefix}-json`;
  const jsonErrorId = `${idPrefix}-json-error`;
  return (
    <div className="space-y-2">
      {advanced ? (
        <div>
          <Label htmlFor={jsonId}>{label} (advanced JSON)</Label>
          <Textarea
            id={jsonId}
            rows={3}
            value={json}
            onChange={(e) => onJsonChange(e.target.value)}
            placeholder={jsonPlaceholder}
            className="mono-input mt-1.5 font-mono text-xs"
            aria-invalid={jsonError ? true : undefined}
            aria-describedby={jsonError ? jsonErrorId : undefined}
          />
          {jsonError ? (
            <p id={jsonErrorId} className="mt-1 text-xs text-destructive" role="alert">
              {jsonError}
            </p>
          ) : null}
        </div>
      ) : (
        <div>
          <Label htmlFor={`${idPrefix}-table-id`}>{label} · Table ID (optional)</Label>
          <Input
            id={`${idPrefix}-table-id`}
            className="mt-1.5"
            value={tableId}
            onChange={(e) => onTableIdChange(e.target.value)}
            placeholder="e.g. a table UUID from Catalog"
          />
        </div>
      )}
      <button
        type="button"
        className="text-xs font-medium text-muted-foreground underline-offset-2 hover:underline"
        onClick={() => onAdvancedChange(!advanced)}
      >
        {advanced ? "Use simple Table ID field" : "Advanced: edit raw JSON"}
      </button>
    </div>
  );
};

export const Intelligence = () => {
  const { api, role } = useAuth();
  const [question, setQuestion] = useState("");
  const [lastAnswer, setLastAnswer] = useState<string | null>(null);
  const [answerMeta, setAnswerMeta] = useState<AnswerMeta | null>(null);
  const [answeredAt, setAnsweredAt] = useState<Date | null>(null);
  const [lastVerifiedQuestionId, setLastVerifiedQuestionId] = useState<string | null>(null);

  const [chatPrompt, setChatPrompt] = useState("");
  const [chatTableId, setChatTableId] = useState("");
  const [chatContext, setChatContext] = useState("{}");
  const [chatAdvancedJson, setChatAdvancedJson] = useState(false);
  const [chatJsonError, setChatJsonError] = useState<string | null>(null);
  const [chatResponse, setChatResponse] = useState<string | null>(null);
  const [chatMeta, setChatMeta] = useState<AnswerMeta | null>(null);
  const [chatRespondedAt, setChatRespondedAt] = useState<Date | null>(null);

  const [mcpTool, setMcpTool] = useState<string>("");
  const [mcpTableId, setMcpTableId] = useState("");
  const [mcpArgs, setMcpArgs] = useState("{}");
  const [mcpAdvancedJson, setMcpAdvancedJson] = useState(false);
  const [mcpJsonError, setMcpJsonError] = useState<string | null>(null);
  const [mcpResult, setMcpResult] = useState<string | null>(null);


  const questions = useQuery({
    queryKey: ["copilot-questions"],
    queryFn: () => getCopilotQuestions(api),
    retry: 1
  });
  const bandit = useQuery({
    queryKey: ["bandit-status"],
    queryFn: () => getBanditStatus(api),
    refetchInterval: 60_000
  });
  const costModel = useQuery({
    queryKey: ["cost-model-evaluate"],
    queryFn: () => getCostModelEvaluate(api),
    refetchInterval: 120_000
  });
  const qpp = useQuery({
    queryKey: ["qpp-confidence"],
    queryFn: () => getQPPConfidence(api),
    refetchInterval: 120_000
  });
  const mcpTools = useQuery({
    queryKey: ["mcp-tools"],
    queryFn: () => listMcpTools(api),
    retry: false
  });

  const verifiedAsk = useMutation({
    mutationFn: (questionId: string) => askCopilotQuestion(api, { question_id: questionId }),
    onSuccess: (data) => {
      setLastAnswer(JSON.stringify(data.result, null, 2));
      setAnswerMeta({ kind: "deterministic", source: "verified", questionId: data.question_id });
      setAnsweredAt(new Date());
    }
  });

  const embeddingSearch = useMutation({
    mutationFn: () => searchCopilot(api, { question: question.trim() }),
    onSuccess: (data) => {
      if (data.answer) {
        setLastAnswer(data.answer);
      } else if (data.result) {
        setLastAnswer(JSON.stringify(data.result, null, 2));
      } else {
        setLastAnswer("No matching verified answer or embedding hit.");
      }
      setAnswerMeta({
        kind: "deterministic",
        source: data.source,
        questionId: data.question_id,
        similarity: data.similarity ?? null
      });
      setAnsweredAt(new Date());
    }
  });

  const naturalAsk = useMutation({
    mutationFn: () => askCopilotNatural(api, { question: question.trim(), include_context: true }),
    onSuccess: (data) => {
      setLastAnswer(data.answer);
      setAnswerMeta({ kind: "generated", provider: data.provider, model: data.model });
      setAnsweredAt(new Date());
    }
  });

  const buildStructuredContext = (tableId: string): Record<string, unknown> => {
    const trimmed = tableId.trim();
    return trimmed ? { table_id: trimmed } : {};
  };

  const directChat = useMutation({
    mutationFn: () => {
      let context: Record<string, unknown> = {};
      if (chatAdvancedJson) {
        try {
          context = JSON.parse(chatContext || "{}");
        } catch {
          setChatJsonError("Context must be valid JSON.");
          throw new Error("Context must be valid JSON.");
        }
      } else {
        context = buildStructuredContext(chatTableId);
      }
      setChatJsonError(null);
      return aiChat(api, { prompt: chatPrompt.trim(), context });
    },
    onSuccess: (data) => {
      setChatResponse(data.response);
      setChatMeta({ kind: "generated", provider: data.provider, model: data.model, tokens: data.tokens_used });
      setChatRespondedAt(new Date());
    }
  });

  const mcpCall = useMutation({
    mutationFn: () => {
      if (!mcpTool) throw new Error("Select a tool.");
      let args: Record<string, unknown> = {};
      if (mcpAdvancedJson) {
        try {
          args = JSON.parse(mcpArgs || "{}");
        } catch {
          setMcpJsonError("Arguments must be valid JSON.");
          throw new Error("Arguments must be valid JSON.");
        }
      } else {
        args = buildStructuredContext(mcpTableId);
      }
      setMcpJsonError(null);
      return callMcpTool(api, { name: mcpTool, arguments: args });
    },
    onSuccess: (data) => setMcpResult(JSON.stringify(data.result, null, 2))
  });

  const banditReset = useMutation({
    mutationFn: () => resetBandit(api),
    onSuccess: () => {
      toast.success("Bandit state reset to heuristics");
      void bandit.refetch();
    }
  });

  const handleSearch = () => {
    if (!question.trim()) return;
    embeddingSearch.mutate();
  };

  const telemetryEmpty = !questions.isLoading && !questions.error && (questions.data ?? []).length === 0;

  return (
    <div>
      <PageHeader
        title="Intelligence"
        subtitle="Verified copilot answers, embedding search, and governor learning signals."
        actions={
          <Link to="/connections">
            <Button variant="secondary">
              <Bot className="size-4" aria-hidden="true" />
              AI providers
            </Button>
          </Link>
        }
      />

      <Alert
        className="page-alert mb-4"
        variant="info"
        title="Operator quick starts: investigate anomalies, estimate savings impact, and pick tables to run next."
      />

      {telemetryEmpty ? <TelemetryRequiredNotice surface="Intelligence recommendations" /> : null}

      <div className="space-y-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle>Ask a question</CardTitle>
            <p className="text-sm text-muted-foreground">
              Start here for day-to-day troubleshooting: verified playbook answers and embedding
              search are deterministic; LLM fallback needs a configured AI provider.
            </p>
          </CardHeader>
          <CardContent className="space-y-3">
            <div>
              <Label htmlFor="intel-question">Question</Label>
              <Textarea
                id="intel-question"
                rows={3}
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder="Ask about fleet health, compaction backlog, or SLA breaches…"
                className="mt-1.5"
                onKeyDown={(e) => {
                  if (!e.shiftKey && e.key === "Enter") {
                    e.preventDefault();
                    handleSearch();
                  }
                }}
              />
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="primary"
                loading={embeddingSearch.isPending}
                onClick={handleSearch}
                disabled={!question.trim()}
              >
                Search (verified + embeddings)
              </Button>
              <Button
                variant="secondary"
                loading={naturalAsk.isPending}
                onClick={() => naturalAsk.mutate()}
                disabled={!question.trim()}
              >
                <Zap className="size-4" aria-hidden="true" />
                Ask LLM (needs provider)
              </Button>
            </div>

            {embeddingSearch.isError || naturalAsk.isError ? (
              <Alert
                variant="error"
                title={
                  embeddingSearch.isError
                    ? "Search failed"
                    : "LLM ask failed"
                }
                description={
                  <div className="flex flex-wrap items-center gap-2">
                    <span>{((embeddingSearch.error ?? naturalAsk.error) as Error).message}</span>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => (embeddingSearch.isError ? embeddingSearch.mutate() : naturalAsk.mutate())}
                    >
                      <RotateCcw className="size-3.5" aria-hidden="true" />
                      Retry
                    </Button>
                  </div>
                }
                onDismiss={() => {
                  embeddingSearch.reset();
                  naturalAsk.reset();
                }}
              />
            ) : null}

            <p className="text-sm text-muted-foreground">Verified playbook questions</p>
            <AsyncBoundary
              isLoading={questions.isLoading}
              isError={Boolean(questions.error)}
              error={questions.error}
              onRetry={() => void questions.refetch()}
              isEmpty={!telemetryEmpty && (questions.data ?? []).length === 0}
              emptyTitle="No playbook loaded"
              emptyDescription="Backend copilot questions unavailable."
            >
              <div className="divide-y divide-border rounded-[var(--radius-default)] border border-border">
                {(questions.data ?? []).map((item) => (
                  <div key={item.id} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
                    <span>{item.prompt}</span>
                    <Button
                      size="sm"
                      variant="ghost"
                      loading={verifiedAsk.isPending && verifiedAsk.variables === item.id}
                      onClick={() => {
                        setLastVerifiedQuestionId(item.id);
                        verifiedAsk.mutate(item.id);
                      }}
                    >
                      Run
                    </Button>
                  </div>
                ))}
              </div>
            </AsyncBoundary>

            {verifiedAsk.isError ? (
              <Alert
                variant="error"
                title="Verified playbook question failed"
                description={
                  <div className="flex flex-wrap items-center gap-2">
                    <span>{(verifiedAsk.error as Error).message}</span>
                    {lastVerifiedQuestionId ? (
                      <Button size="sm" variant="ghost" onClick={() => verifiedAsk.mutate(lastVerifiedQuestionId)}>
                        <RotateCcw className="size-3.5" aria-hidden="true" />
                        Retry
                      </Button>
                    ) : null}
                  </div>
                }
                onDismiss={() => verifiedAsk.reset()}
              />
            ) : null}

            {lastAnswer ? (
              <div className="space-y-2">
                {answerMeta ? <AnswerMetaRow meta={answerMeta} respondedAt={answeredAt} /> : null}
                {lastAnswer.trim().startsWith("{") || lastAnswer.trim().startsWith("[") ? (
                  <JsonBlock value={lastAnswer} title="Answer" />
                ) : (
                  <Card>
                    <CardHeader className="pb-2">
                      <CardTitle className="text-sm">Answer</CardTitle>
                    </CardHeader>
                    <CardContent>
                      <p className="m-0 whitespace-pre-wrap text-sm">{lastAnswer}</p>
                    </CardContent>
                  </Card>
                )}
              </div>
            ) : null}
          </CardContent>
        </Card>

        <details className="rounded-[var(--radius-default)] border border-border">
          <summary className="cursor-pointer px-4 py-3 text-sm font-medium">Chat &amp; tool calls (advanced)</summary>
          <div className="grid gap-4 border-t border-border p-4 lg:grid-cols-2">
            <Card>
              <CardHeader className="flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm">Direct AI chat</CardTitle>
                <Bot className="size-4 text-muted-foreground" aria-hidden="true" />
              </CardHeader>
              <CardContent className="space-y-3">
                <p className="text-sm text-muted-foreground">
                  Uses the default AI provider via <code>/ai/chat</code> with optional context.
                </p>
                <div>
                  <Label htmlFor="intel-chat-prompt">Prompt</Label>
                  <Textarea
                    id="intel-chat-prompt"
                    rows={3}
                    value={chatPrompt}
                    onChange={(e) => setChatPrompt(e.target.value)}
                    placeholder="Ask anything about your datalake…"
                    className="mt-1.5"
                  />
                </div>
                <StructuredJsonEditor
                  idPrefix="intel-chat-context"
                  label="Context"
                  tableId={chatTableId}
                  onTableIdChange={setChatTableId}
                  advanced={chatAdvancedJson}
                  onAdvancedChange={setChatAdvancedJson}
                  json={chatContext}
                  onJsonChange={setChatContext}
                  jsonError={chatJsonError}
                  jsonPlaceholder='Optional context JSON, e.g. {"table_id": "..."}'
                />
                <Button
                  variant="primary"
                  loading={directChat.isPending}
                  disabled={!chatPrompt.trim()}
                  onClick={() => directChat.mutate()}
                >
                  Send
                </Button>
                {directChat.isError ? (
                  <Alert
                    variant="error"
                    title="Chat request failed"
                    description={
                      <div className="flex flex-wrap items-center gap-2">
                        <span>{(directChat.error as Error).message}</span>
                        <Button size="sm" variant="ghost" onClick={() => directChat.mutate()}>
                          <RotateCcw className="size-3.5" aria-hidden="true" />
                          Retry
                        </Button>
                      </div>
                    }
                    onDismiss={() => directChat.reset()}
                  />
                ) : null}
                {chatResponse ? (
                  <div className="space-y-2">
                    {chatMeta ? <AnswerMetaRow meta={chatMeta} respondedAt={chatRespondedAt} /> : null}
                    <Card>
                      <CardHeader className="pb-2">
                        <CardTitle className="text-sm">Response</CardTitle>
                      </CardHeader>
                      <CardContent>
                        <p className="m-0 whitespace-pre-wrap text-sm">{chatResponse}</p>
                      </CardContent>
                    </Card>
                  </div>
                ) : null}
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm">MCP tools</CardTitle>
                <Wrench className="size-4 text-muted-foreground" aria-hidden="true" />
              </CardHeader>
              <CardContent>
                <AsyncBoundary
                  isLoading={mcpTools.isLoading}
                  isError={Boolean(mcpTools.error)}
                  error={mcpTools.error}
                  onRetry={() => void mcpTools.refetch()}
                >
                  <div className="space-y-3">
                    <div>
                      <Label htmlFor="intel-mcp-tool">Tool</Label>
                      <select
                        id="intel-mcp-tool"
                        className="mt-1.5 h-[var(--control-height)] w-full rounded-[var(--radius-default)] border border-border bg-background px-3 text-sm"
                        value={mcpTool}
                        onChange={(event) => setMcpTool(event.target.value)}
                      >
                        <option value="">Select tool</option>
                        {(mcpTools.data ?? []).map((tool) => (
                          <option key={tool.name} value={tool.name} title={tool.description}>
                            {tool.name}
                          </option>
                        ))}
                      </select>
                    </div>
                    {mcpTool ? (
                      <p className="text-sm text-muted-foreground">
                        {(mcpTools.data ?? []).find((tool) => tool.name === mcpTool)?.description}
                      </p>
                    ) : null}
                    <StructuredJsonEditor
                      idPrefix="intel-mcp-args"
                      label="Arguments"
                      tableId={mcpTableId}
                      onTableIdChange={setMcpTableId}
                      advanced={mcpAdvancedJson}
                      onAdvancedChange={setMcpAdvancedJson}
                      json={mcpArgs}
                      onJsonChange={setMcpArgs}
                      jsonError={mcpJsonError}
                      jsonPlaceholder='{"table_id": "..."}'
                    />
                    <Button
                      variant="primary"
                      loading={mcpCall.isPending}
                      disabled={!mcpTool || role === "viewer"}
                      onClick={() => mcpCall.mutate()}
                    >
                      Call tool
                    </Button>
                    {mcpCall.isError ? (
                      <Alert
                        variant="error"
                        title="Tool call failed"
                        description={
                          <div className="flex flex-wrap items-center gap-2">
                            <span>{(mcpCall.error as Error).message}</span>
                            <Button size="sm" variant="ghost" onClick={() => mcpCall.mutate()}>
                              <RotateCcw className="size-3.5" aria-hidden="true" />
                              Retry
                            </Button>
                          </div>
                        }
                        onDismiss={() => mcpCall.reset()}
                      />
                    ) : null}
                    {mcpResult ? <JsonBlock value={mcpResult} title="Result" /> : null}
                  </div>
                </AsyncBoundary>
              </CardContent>
            </Card>
          </div>
        </details>

        <details className="rounded-[var(--radius-default)] border border-border">
          <summary className="cursor-pointer px-4 py-3 text-sm font-medium">
            Governor learning diagnostics (admin)
          </summary>
          <div className="grid gap-4 border-t border-border p-4 lg:grid-cols-3">
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">Governor bandit</CardTitle>
              </CardHeader>
              <CardContent>
                <AsyncBoundary
                  isLoading={bandit.isLoading}
                  isError={Boolean(bandit.error)}
                  error={bandit.error}
                  onRetry={() => void bandit.refetch()}
                  isEmpty={!bandit.data}
                  emptyTitle="Bandit unavailable"
                  emptyDescription="Governor state file not readable."
                >
                  {bandit.data ? (
                    <div className="space-y-4">
                      <div className="grid grid-cols-2 gap-4">
                        <StatPair label="Observations" value={bandit.data.observations} />
                        <StatPair
                          label="Confident"
                          value={
                            <span className={bandit.data.confident ? "text-[var(--color-status-success)]" : undefined}>
                              {bandit.data.confident ? "Yes" : "No"}
                            </span>
                          }
                        />
                      </div>
                      <DescriptionList
                        items={[
                          { label: "High load ratio", value: bandit.data.config.high_load_ratio?.toFixed(3) ?? "—" },
                          {
                            label: "High load fraction",
                            value: bandit.data.config.high_load_fraction?.toFixed(3) ?? "—"
                          },
                          { label: "Per-replica cap", value: formatNumber(bandit.data.config.per_replica_cap) }
                        ]}
                      />
                      {!bandit.data.confident ? (
                        <Alert
                          variant="info"
                          title="Still learning — using heuristic admission until enough outcomes are recorded."
                        />
                      ) : null}
                      {role === "admin" ? (
                        <ConfirmAction
                          title="Reset admission bandit?"
                          description="Clears learned governor bandit state and reverts to static heuristics."
                          onConfirm={() => banditReset.mutate()}
                        >
                          <Button size="sm" variant="danger" loading={banditReset.isPending}>
                            Reset bandit
                          </Button>
                        </ConfirmAction>
                      ) : null}
                      {banditReset.isError ? (
                        <Alert
                          variant="error"
                          title="Reset failed"
                          description={(banditReset.error as Error).message}
                          onDismiss={() => banditReset.reset()}
                        />
                      ) : null}
                    </div>
                  ) : null}
                </AsyncBoundary>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">Learned cost model</CardTitle>
              </CardHeader>
              <CardContent>
                <AsyncBoundary
                  isLoading={costModel.isLoading}
                  isError={Boolean(costModel.error)}
                  error={costModel.error}
                  onRetry={() => void costModel.refetch()}
                  isEmpty={!costModel.data}
                  emptyTitle="Cost model unavailable"
                  emptyDescription="No audit outcomes to evaluate yet."
                >
                  {costModel.data ? (
                    <div className="space-y-4">
                      <div className="grid grid-cols-2 gap-4">
                        <StatPair label="MAE (learned)" value={`${costModel.data.mae_learned.toFixed(2)} USD/mo`} />
                        <StatPair label="MAE (heuristic)" value={`${costModel.data.mae_heuristic.toFixed(2)} USD/mo`} />
                        <StatPair
                          label="Evaluated holdout"
                          value={`${costModel.data.n_evaluated} / ${costModel.data.n_samples}`}
                        />
                        <StatPair
                          label="Conformal coverage"
                          value={`${(costModel.data.band_coverage * 100).toFixed(0)}%`}
                        />
                        <StatPair
                          label="Outcome lineage"
                          value={`${(costModel.data.lineage_completeness * 100).toFixed(0)}%`}
                        />
                        <StatPair
                          label="Complete / eligible"
                          value={`${costModel.data.lineage_complete} / ${costModel.data.lineage_eligible}`}
                        />
                        <StatPair
                          label="Domain abstention"
                          value={`${(costModel.data.abstention_rate * 100).toFixed(0)}%`}
                        />
                      </div>
                      <Badge variant={costModel.data.beats_heuristic ? "success" : "secondary"}>
                        {costModel.data.beats_heuristic
                          ? "Learned model beats heuristic on holdout"
                          : "Heuristic still competitive — need more outcome labels"}
                      </Badge>
                      {costModel.data.n_samples < 10 ? (
                        <Alert
                          variant="warning"
                          title="Few labeled maintenance outcomes — projections still mostly heuristic."
                        />
                      ) : null}
                      {costModel.data.lineage_completeness < 0.9 ? (
                        <Alert
                          variant="warning"
                          title="Outcome lineage is below the 90% evidence gate. Missing post-scans are excluded from calibration."
                        />
                      ) : null}
                      {costModel.data.abstentions > 0 ? (
                        <Alert
                          variant="info"
                          title={`${costModel.data.abstentions} holdout outcomes were outside the training domain and excluded from learned-model accuracy.`}
                        />
                      ) : null}
                    </div>
                  ) : null}
                </AsyncBoundary>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">Query performance prediction (QPP)</CardTitle>
              </CardHeader>
              <CardContent>
                <AsyncBoundary
                  isLoading={qpp.isLoading}
                  isError={Boolean(qpp.error)}
                  error={qpp.error}
                  onRetry={() => void qpp.refetch()}
                  isEmpty={!qpp.data}
                  emptyTitle="QPP unavailable"
                  emptyDescription="No query performance predictions recorded yet."
                >
                  {qpp.data ? (
                    <div className="space-y-4">
                      <Badge variant={qpp.data.qpp_enabled ? "success" : "secondary"}>
                        {qpp.data.qpp_enabled ? "Admission gating enabled" : "Advisory only — not gating admission"}
                      </Badge>
                      <div className="grid grid-cols-2 gap-4">
                        <StatPair label="Observations" value={qpp.data.observations} />
                        <StatPair label="Confidence" value={`${(qpp.data.confidence * 100).toFixed(0)}%`} />
                      </div>
                      <DescriptionList
                        items={[
                          {
                            label: "Using trained model",
                            value: qpp.data.using_trained_model ? "Yes" : "No — falling back to defaults"
                          },
                          { label: "Min observations to train", value: formatNumber(qpp.data.min_observations) }
                        ]}
                      />
                      {!qpp.data.using_trained_model ? (
                        <Alert
                          variant="info"
                          title="Still collecting observations — the governor uses default latency estimates until enough runs are recorded."
                        />
                      ) : null}
                    </div>
                  ) : null}
                </AsyncBoundary>
              </CardContent>
            </Card>
          </div>
        </details>
      </div>
    </div>
  );
};
