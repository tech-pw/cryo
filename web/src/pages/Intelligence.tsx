import { useMutation, useQuery } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Input,
  List,
  Row,
  Select,
  Space,
  Spin,
  Statistic,
  Tag,
  Typography,
  message
} from "antd";
import { RobotOutlined, ThunderboltOutlined, ToolOutlined } from "@ant-design/icons";
import { useState } from "react";
import { Link } from "react-router-dom";
import {
  aiChat,
  askCopilotNatural,
  askCopilotQuestion,
  callMcpTool,
  getBanditStatus,
  getCopilotQuestions,
  getCostModelEvaluate,
  listMcpTools,
  resetBandit,
  searchCopilot
} from "../api/client";
import { ConfirmAction } from "../components/confirm";
import { EmptyState, PageHeader, QueryErrorAlert } from "../components/common";
import { useAuth } from "../lib/auth";
import { formatNumber } from "../lib/format";

const { TextArea } = Input;

export const Intelligence = () => {
  const { api, role } = useAuth();
  const [question, setQuestion] = useState("");
  const [lastAnswer, setLastAnswer] = useState<string | null>(null);
  const [answerMeta, setAnswerMeta] = useState<string | null>(null);
  const [chatPrompt, setChatPrompt] = useState("");
  const [chatContext, setChatContext] = useState("{}");
  const [chatResponse, setChatResponse] = useState<string | null>(null);
  const [chatMeta, setChatMeta] = useState<string | null>(null);
  const [mcpTool, setMcpTool] = useState<string | undefined>();
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
    onError: (error) => message.error((error as Error).message)
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
    onError: (error) => message.error((error as Error).message)
  });

  const naturalAsk = useMutation({
    mutationFn: () => askCopilotNatural(api, { question: question.trim(), include_context: true }),
    onSuccess: (data) => {
      setLastAnswer(data.answer);
      setAnswerMeta(`${data.provider} · ${data.model}`);
    },
    onError: (error) => message.error((error as Error).message)
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
    onError: (error) => message.error((error as Error).message)
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
    onError: (error) => message.error((error as Error).message)
  });

  const banditReset = useMutation({
    mutationFn: () => resetBandit(api),
    onSuccess: () => {
      message.success("Bandit state reset to heuristics");
      void bandit.refetch();
    },
    onError: (error) => message.error((error as Error).message)
  });

  const handleSearch = () => {
    if (!question.trim()) return;
    embeddingSearch.mutate();
  };

  return (
    <div>
      <PageHeader
        title="Intelligence"
        subtitle="Verified copilot answers, embedding search, governor bandit tuning, and learned cost model metrics."
        actions={
          <Link to="/connections">
            <Button icon={<RobotOutlined />}>AI providers</Button>
          </Link>
        }
      />
      <Alert
        className="page-alert"
        type="info"
        showIcon
        message="Operator quick starts: investigate anomalies, estimate savings impact, and pick tables to run next."
      />

      <QueryErrorAlert error={questions.error} title="Failed to load copilot questions" />
      <QueryErrorAlert error={bandit.error} title="Failed to load governor bandit status" />
      <QueryErrorAlert error={costModel.error} title="Failed to load cost model evaluation" />

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={14}>
          <Card title="Cryo copilot">
            <Space direction="vertical" className="full-width" size={12}>
              <TextArea
                rows={3}
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder="Ask about fleet health, compaction backlog, or SLA breaches…"
                onPressEnter={(e) => {
                  if (!e.shiftKey) {
                    e.preventDefault();
                    handleSearch();
                  }
                }}
              />
              <Space wrap>
                <Button
                  type="primary"
                  loading={embeddingSearch.isPending}
                  onClick={handleSearch}
                  disabled={!question.trim()}
                >
                  Search (verified + embeddings)
                </Button>
                <Button
                  icon={<ThunderboltOutlined />}
                  loading={naturalAsk.isPending}
                  onClick={() => naturalAsk.mutate()}
                  disabled={!question.trim()}
                >
                  Ask LLM (needs provider)
                </Button>
              </Space>

              <Typography.Text type="secondary">Verified playbook questions</Typography.Text>
              {questions.isLoading ? (
                <Spin />
              ) : (questions.data ?? []).length ? (
                <List
                  size="small"
                  bordered
                  dataSource={questions.data ?? []}
                  renderItem={(item) => (
                    <List.Item
                      actions={[
                        <Button
                          key="ask"
                          type="link"
                          size="small"
                          loading={verifiedAsk.isPending && verifiedAsk.variables === item.id}
                          onClick={() => verifiedAsk.mutate(item.id)}
                        >
                          Run
                        </Button>
                      ]}
                    >
                      <List.Item.Meta title={item.prompt} />
                    </List.Item>
                  )}
                />
              ) : (
                <EmptyState title="No playbook loaded" description="Backend copilot questions unavailable." />
              )}

              {lastAnswer ? (
                <Card size="small" title={answerMeta ?? "Answer"} type="inner">
                  <Typography.Paragraph style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}>
                    {lastAnswer}
                  </Typography.Paragraph>
                </Card>
              ) : null}
            </Space>
          </Card>
        </Col>

        <Col xs={24} lg={10}>
          <Space direction="vertical" className="full-width" size={16}>
            <Card title="Governor bandit" loading={bandit.isLoading}>
              {bandit.data ? (
                <>
                  <Row gutter={16}>
                    <Col span={12}>
                      <Statistic title="Observations" value={bandit.data.observations} />
                    </Col>
                    <Col span={12}>
                      <Statistic
                        title="Confident"
                        value={bandit.data.confident ? "Yes" : "No"}
                        valueStyle={{ color: bandit.data.confident ? "#52c41a" : undefined }}
                      />
                    </Col>
                  </Row>
                  <Descriptions size="small" column={1} style={{ marginTop: 16 }}>
                    <Descriptions.Item label="High load ratio">
                      {bandit.data.config.high_load_ratio?.toFixed(3) ?? "—"}
                    </Descriptions.Item>
                    <Descriptions.Item label="High load fraction">
                      {bandit.data.config.high_load_fraction?.toFixed(3) ?? "—"}
                    </Descriptions.Item>
                    <Descriptions.Item label="Per-replica cap">
                      {formatNumber(bandit.data.config.per_replica_cap)}
                    </Descriptions.Item>
                  </Descriptions>
                  {!bandit.data.confident ? (
                    <Alert
                      style={{ marginTop: 12 }}
                      type="info"
                      showIcon
                      message="Still learning — using heuristic admission until enough outcomes are recorded."
                    />
                  ) : null}
                  {role === "admin" ? (
                    <ConfirmAction
                      title="Reset admission bandit?"
                      description="Clears learned governor bandit state and reverts to static heuristics."
                      onConfirm={() => banditReset.mutate()}
                    >
                      <Button
                        size="small"
                        danger
                        style={{ marginTop: 12 }}
                        loading={banditReset.isPending}
                      >
                        Reset bandit
                      </Button>
                    </ConfirmAction>
                  ) : null}
                </>
              ) : (
                <EmptyState title="Bandit unavailable" description="Governor state file not readable." />
              )}
            </Card>

            <Card title="Learned cost model" loading={costModel.isLoading}>
              {costModel.data ? (
                <>
                  <Row gutter={[16, 16]}>
                    <Col span={12}>
                      <Statistic
                        title="MAE (learned)"
                        value={costModel.data.mae_learned}
                        precision={2}
                        suffix="USD/mo"
                      />
                    </Col>
                    <Col span={12}>
                      <Statistic
                        title="MAE (heuristic)"
                        value={costModel.data.mae_heuristic}
                        precision={2}
                        suffix="USD/mo"
                      />
                    </Col>
                    <Col span={12}>
                      <Statistic title="Training samples" value={costModel.data.n_samples} />
                    </Col>
                    <Col span={12}>
                      <Statistic
                        title="Conformal coverage"
                        value={(costModel.data.band_coverage * 100).toFixed(0)}
                        suffix="%"
                      />
                    </Col>
                  </Row>
                  <Tag
                    color={costModel.data.beats_heuristic ? "success" : "default"}
                    style={{ marginTop: 12 }}
                  >
                    {costModel.data.beats_heuristic
                      ? "Learned model beats heuristic on holdout"
                      : "Heuristic still competitive — need more outcome labels"}
                  </Tag>
                  {costModel.data.n_samples < 10 ? (
                    <Alert
                      style={{ marginTop: 12 }}
                      type="warning"
                      showIcon
                      message="Few labeled maintenance outcomes — projections still mostly heuristic."
                    />
                  ) : null}
                </>
              ) : (
                <EmptyState title="Cost model unavailable" description="No audit outcomes to evaluate yet." />
              )}
            </Card>
          </Space>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={12}>
          <Card title="MCP tools" extra={<ToolOutlined />}>
            <QueryErrorAlert error={mcpTools.error} title="Failed to load MCP tools" />
            <Space direction="vertical" className="full-width" size={12}>
              <Select
                placeholder="Select tool"
                value={mcpTool}
                loading={mcpTools.isLoading}
                options={(mcpTools.data ?? []).map((tool) => ({
                  value: tool.name,
                  label: tool.name,
                  title: tool.description
                }))}
                onChange={setMcpTool}
                style={{ width: "100%" }}
              />
              {mcpTool ? (
                <Typography.Text type="secondary">
                  {(mcpTools.data ?? []).find((tool) => tool.name === mcpTool)?.description}
                </Typography.Text>
              ) : null}
              <Input.TextArea
                rows={4}
                value={mcpArgs}
                onChange={(e) => setMcpArgs(e.target.value)}
                placeholder='{"table_id": "..."}'
                className="mono-input"
              />
              <Button
                type="primary"
                loading={mcpCall.isPending}
                disabled={!mcpTool || role === "viewer"}
                onClick={() => mcpCall.mutate()}
              >
                Call tool
              </Button>
              {mcpResult ? (
                <Card size="small" type="inner" title="Result">
                  <Typography.Paragraph style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}>
                    <pre className="inline-json">{mcpResult}</pre>
                  </Typography.Paragraph>
                </Card>
              ) : null}
            </Space>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="Direct AI chat" extra={<RobotOutlined />}>
            <Space direction="vertical" className="full-width" size={12}>
              <Typography.Text type="secondary">
                Uses the default AI provider via <Typography.Text code>/ai/chat</Typography.Text> with optional JSON context.
              </Typography.Text>
              <TextArea
                rows={3}
                value={chatPrompt}
                onChange={(e) => setChatPrompt(e.target.value)}
                placeholder="Ask anything about your datalake…"
              />
              <Input.TextArea
                rows={3}
                value={chatContext}
                onChange={(e) => setChatContext(e.target.value)}
                placeholder='Optional context JSON, e.g. {"table_id": "..."}'
                className="mono-input"
              />
              <Button
                type="primary"
                loading={directChat.isPending}
                disabled={!chatPrompt.trim()}
                onClick={() => directChat.mutate()}
              >
                Send
              </Button>
              {chatResponse ? (
                <Card size="small" type="inner" title={chatMeta ?? "Response"}>
                  <Typography.Paragraph style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}>
                    {chatResponse}
                  </Typography.Paragraph>
                </Card>
              ) : null}
            </Space>
          </Card>
        </Col>
      </Row>
    </div>
  );
};
