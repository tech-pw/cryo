import { useMutation, useQuery } from "@tanstack/react-query";
import { Bot, Wrench, Zap } from "lucide-react";
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

export const Intelligence = () => {
  const { api, role } = useAuth();
  const [question, setQuestion] = useState("");
  const [lastAnswer, setLastAnswer] = useState<string | null>(null);
  const [answerMeta, setAnswerMeta] = useState<string | null>(null);
  const [chatPrompt, setChatPrompt] = useState("");
  const [chatContext, setChatContext] = useState("{}");
  const [chatResponse, setChatResponse] = useState<string | null>(null);
  const [chatMeta, setChatMeta] = useState<string | null>(null);
  const [mcpTool, setMcpTool] = useState<string>("");
  const [mcpArgs, setMcpArgs] = useState("{}");
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
      setAnswerMeta(`Verified playbook · ${data.question_id}`);
    },
    onError: (error) => toast.error((error as Error).message)
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
      setAnswerMeta(
        `Source: ${data.source}${data.similarity != null ? ` · similarity ${(data.similarity * 100).toFixed(0)}%` : ""}`
      );
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const naturalAsk = useMutation({
    mutationFn: () => askCopilotNatural(api, { question: question.trim(), include_context: true }),
    onSuccess: (data) => {
      setLastAnswer(data.answer);
      setAnswerMeta(`${data.provider} · ${data.model}`);
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const directChat = useMutation({
    mutationFn: () => {
      let context = {};
      try {
        context = JSON.parse(chatContext || "{}");
      } catch {
        throw new Error("Context must be valid JSON.");
      }
      return aiChat(api, { prompt: chatPrompt.trim(), context });
    },
    onSuccess: (data) => {
      setChatResponse(data.response);
      setChatMeta(`${data.provider} · ${data.model} · ${data.tokens_used} tokens`);
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const mcpCall = useMutation({
    mutationFn: () => {
      if (!mcpTool) throw new Error("Select a tool.");
      let args = {};
      try {
        args = JSON.parse(mcpArgs || "{}");
      } catch {
        throw new Error("Arguments must be valid JSON.");
      }
      return callMcpTool(api, { name: mcpTool, arguments: args });
    },
    onSuccess: (data) => setMcpResult(JSON.stringify(data.result, null, 2)),
    onError: (error) => toast.error((error as Error).message)
  });

  const banditReset = useMutation({
    mutationFn: () => resetBandit(api),
    onSuccess: () => {
      toast.success("Bandit state reset to heuristics");
      void bandit.refetch();
    },
    onError: (error) => toast.error((error as Error).message)
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
            <CardTitle>Tier 1 · Copilot & search</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <Textarea
              rows={3}
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="Ask about fleet health, compaction backlog, or SLA breaches…"
              onKeyDown={(e) => {
                if (!e.shiftKey && e.key === "Enter") {
                  e.preventDefault();
                  handleSearch();
                }
              }}
            />
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
                      onClick={() => verifiedAsk.mutate(item.id)}
                    >
                      Run
                    </Button>
                  </div>
                ))}
              </div>
            </AsyncBoundary>

            {lastAnswer ? (
              lastAnswer.trim().startsWith("{") || lastAnswer.trim().startsWith("[") ? (
                <JsonBlock value={lastAnswer} title={answerMeta ?? "Answer"} />
              ) : (
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm">{answerMeta ?? "Answer"}</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <p className="m-0 whitespace-pre-wrap text-sm">{lastAnswer}</p>
                  </CardContent>
                </Card>
              )
            ) : null}
          </CardContent>
        </Card>

        <details className="rounded-[var(--radius-default)] border border-border">
          <summary className="cursor-pointer px-4 py-3 text-sm font-medium">Tier 2 · More tools (LLM chat & MCP)</summary>
          <div className="grid gap-4 border-t border-border p-4 lg:grid-cols-2">
            <Card>
              <CardHeader className="flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm">Direct AI chat</CardTitle>
                <Bot className="size-4 text-muted-foreground" aria-hidden="true" />
              </CardHeader>
              <CardContent className="space-y-3">
                <p className="text-sm text-muted-foreground">
                  Uses the default AI provider via <code>/ai/chat</code> with optional JSON context.
                </p>
                <Textarea
                  rows={3}
                  value={chatPrompt}
                  onChange={(e) => setChatPrompt(e.target.value)}
                  placeholder="Ask anything about your datalake…"
                />
                <Textarea
                  rows={3}
                  value={chatContext}
                  onChange={(e) => setChatContext(e.target.value)}
                  placeholder='Optional context JSON, e.g. {"table_id": "..."}'
                  className="mono-input font-mono text-xs"
                />
                <Button
                  variant="primary"
                  loading={directChat.isPending}
                  disabled={!chatPrompt.trim()}
                  onClick={() => directChat.mutate()}
                >
                  Send
                </Button>
                {chatResponse ? (
                  <Card>
                    <CardHeader className="pb-2">
                      <CardTitle className="text-sm">{chatMeta ?? "Response"}</CardTitle>
                    </CardHeader>
                    <CardContent>
                      <p className="m-0 whitespace-pre-wrap text-sm">{chatResponse}</p>
                    </CardContent>
                  </Card>
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
                    <select
                      className="h-[var(--control-height)] w-full rounded-[var(--radius-default)] border border-border bg-background px-3 text-sm"
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
                    {mcpTool ? (
                      <p className="text-sm text-muted-foreground">
                        {(mcpTools.data ?? []).find((tool) => tool.name === mcpTool)?.description}
                      </p>
                    ) : null}
                    <Textarea
                      rows={4}
                      value={mcpArgs}
                      onChange={(e) => setMcpArgs(e.target.value)}
                      placeholder='{"table_id": "..."}'
                      className="mono-input font-mono text-xs"
                    />
                    <Button
                      variant="primary"
                      loading={mcpCall.isPending}
                      disabled={!mcpTool || role === "viewer"}
                      onClick={() => mcpCall.mutate()}
                    >
                      Call tool
                    </Button>
                    {mcpResult ? <JsonBlock value={mcpResult} title="Result" /> : null}
                  </div>
                </AsyncBoundary>
              </CardContent>
            </Card>
          </div>
        </details>

        <details className="rounded-[var(--radius-default)] border border-border">
          <summary className="cursor-pointer px-4 py-3 text-sm font-medium">
            Tier 3 · Advanced telemetry (admin)
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
                        <StatPair label="Training samples" value={costModel.data.n_samples} />
                        <StatPair
                          label="Conformal coverage"
                          value={`${(costModel.data.band_coverage * 100).toFixed(0)}%`}
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
