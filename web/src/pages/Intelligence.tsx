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
  Space,
  Spin,
  Statistic,
  Tag,
  Typography,
  message
} from "antd";
import { RobotOutlined, ThunderboltOutlined } from "@ant-design/icons";
import { useState } from "react";
import { Link } from "react-router-dom";
import {
  askCopilotNatural,
  askCopilotQuestion,
  getBanditStatus,
  getCopilotQuestions,
  getCostModelEvaluate,
  searchCopilot
} from "../api/client";
import { EmptyState, PageHeader, QueryErrorAlert } from "../components/common";
import { useAuth } from "../lib/auth";
import { formatNumber } from "../lib/format";

const { TextArea } = Input;

export const Intelligence = () => {
  const { api } = useAuth();
  const [question, setQuestion] = useState("");
  const [lastAnswer, setLastAnswer] = useState<string | null>(null);
  const [answerMeta, setAnswerMeta] = useState<string | null>(null);

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
    </div>
  );
};
