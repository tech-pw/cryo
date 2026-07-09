import { useMutation, useQuery } from "@tanstack/react-query";
import {
  Card,
  Descriptions,
  Tag,
  Typography,
  Spin,
  Alert,
  Row,
  Col,
  Statistic,
  Button,
  Space,
  Collapse,
  message,
} from "antd";
import {
  ClockCircleOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  WarningOutlined,
  ArrowLeftOutlined,
  DatabaseOutlined,
  CodeOutlined,
  FilterOutlined,
  LikeOutlined,
  DislikeOutlined,
  RobotOutlined,
} from "@ant-design/icons";
import { Link, useParams, useNavigate } from "react-router-dom";
import {
  diagnoseRunFailure,
  getLatestRunDiagnosis,
  getJob,
  getJobDetailDashboard,
  getResourcePrediction,
  submitDiagnosisFeedback,
  type JobDetailDashboard
} from "../api/client";
import { PageHeader, QueryErrorAlert } from "../components/common";
import { StatusTag } from "../components/status";
import { useAuth } from "../lib/auth";
import { formatDuration } from "../lib/format";
import { engineTagColor, formatEngineLabel, isNativeStatement } from "../lib/engines";

const { Title, Text, Paragraph } = Typography;

interface JobDetail extends JobDetailDashboard {}

const formatJobDuration = (seconds: number | null) => {
  if (!seconds) return "—";
  return formatDuration(seconds * 1000);
};

export const JobDetail = () => {
  const { jobId } = useParams<{ jobId: string }>();
  const navigate = useNavigate();
  const { api } = useAuth();

  const { data, isLoading, error } = useQuery<JobDetail>({
    queryKey: ["job-detail", api.token, jobId],
    queryFn: () => getJobDetailDashboard(api, jobId!),
    enabled: Boolean(jobId)
  });
  const rawJob = useQuery({
    queryKey: ["job-raw", api.token, jobId],
    queryFn: () => getJob(api, jobId!),
    enabled: Boolean(jobId),
    retry: false
  });
  const resourcePrediction = useQuery({
    queryKey: ["resource-prediction", data?.table_id],
    queryFn: () => getResourcePrediction(api, data!.table_id),
    enabled: Boolean(data?.table_id),
    retry: false
  });
  const latestDiagnosis = useQuery({
    queryKey: ["run-diagnosis-latest", data?.run_id],
    queryFn: () => getLatestRunDiagnosis(api, data!.run_id),
    enabled: Boolean(data?.run_id),
    retry: false
  });
  const diagnose = useMutation({
    mutationFn: () => diagnoseRunFailure(api, data!.run_id, { force: true }),
    onSuccess: () => {
      void latestDiagnosis.refetch();
    },
    onError: (err) => message.error((err as Error).message)
  });
  const diagnosisRecord = latestDiagnosis.data ?? diagnose.data ?? null;
  const diagnosisFeedback = useMutation({
    mutationFn: (feedback: "up" | "down") =>
      submitDiagnosisFeedback(api, diagnosisRecord!.id, { feedback }),
    onSuccess: () => message.success("Diagnosis feedback recorded"),
    onError: (err) => message.error((err as Error).message)
  });

  if (isLoading) {
    return (
      <div style={{ textAlign: "center", padding: "60px" }}>
        <Spin size="large" />
      </div>
    );
  }

  if (error || !data) {
    return <QueryErrorAlert error={error ?? new Error("Job not found")} title="Failed to load job details" />;
  }

  return (
    <div>
      <Button
        icon={<ArrowLeftOutlined />}
        onClick={() => navigate("/runs")}
        style={{ marginBottom: 16 }}
      >
        Back to Runs
      </Button>
      <PageHeader
        title="Maintenance job detail"
        subtitle={`Job ID: ${data.job_id}`}
        actions={
          <Space>
            <StatusTag status={data.status} />
            <Link to={`/tables/${data.table_id}`}>
              <Button size="small">Open table</Button>
            </Link>
            <Link to={`/tables/${data.table_id}?view=savings`}>
              <Button size="small" type="primary">Re-run from table</Button>
            </Link>
          </Space>
        }
      />

      {data.error_message && (
        <Alert
          title="Job Failed"
          description={data.error_message}
          type="error"
          showIcon
          icon={<WarningOutlined />}
          style={{ marginBottom: 24 }}
        />
      )}
      {data.status === "failed" ? (
        <Card
          title={
            <Space>
              <RobotOutlined />
              Failure diagnosis
            </Space>
          }
          style={{ marginBottom: 16 }}
          extra={
            <Button loading={diagnose.isPending} onClick={() => diagnose.mutate()}>
              Diagnose
            </Button>
          }
        >
          {diagnosisRecord ? (
            <Space direction="vertical" style={{ width: "100%" }}>
              <Typography.Text strong>
                {diagnosisRecord.diagnosis_class.replaceAll("_", " ")} ({Math.round(diagnosisRecord.confidence * 100)}% confidence)
              </Typography.Text>
              {diagnosisRecord.summary ? (
                <Typography.Text>{diagnosisRecord.summary}</Typography.Text>
              ) : null}
              {diagnosisRecord.runbook_step ? (
                <Alert type="info" showIcon title="Runbook step" description={diagnosisRecord.runbook_step} />
              ) : null}
              {diagnosisRecord.evidence.length ? (
                <ul style={{ margin: 0, paddingInlineStart: 16 }}>
                  {diagnosisRecord.evidence.map((line, idx) => (
                    <li key={`${idx}-${line}`}>
                      <Typography.Text type="secondary">{line}</Typography.Text>
                    </li>
                  ))}
                </ul>
              ) : null}
              <Space>
                <Button
                  size="small"
                  icon={<LikeOutlined />}
                  disabled={!diagnosisRecord}
                  loading={diagnosisFeedback.isPending}
                  onClick={() => diagnosisFeedback.mutate("up")}
                >
                  Helpful
                </Button>
                <Button
                  size="small"
                  icon={<DislikeOutlined />}
                  disabled={!diagnosisRecord}
                  loading={diagnosisFeedback.isPending}
                  onClick={() => diagnosisFeedback.mutate("down")}
                >
                  Not helpful
                </Button>
              </Space>
            </Space>
          ) : (
            <Typography.Text type="secondary">
              Run diagnosis uses run/job/audit evidence. Trigger when triaging failed jobs.
            </Typography.Text>
          )}
        </Card>
      ) : null}

      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col span={6}>
          <Card>
            <Statistic
              title="Duration"
              value={formatJobDuration(data.duration_seconds)}
              prefix={<ClockCircleOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="Input Files"
              value={data.input_files_count || 0}
              styles={{ content: { color: "#1890ff" }}}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="Output Files"
              value={data.output_files_count || 0}
              styles={{ content: { color: "#52c41a" }}}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="Effectiveness Ratio"
              value={data.effectiveness_ratio?.toFixed(1) || "—"}
              suffix=":1"
              styles={{
                content: {
                  color: (data.effectiveness_ratio || 0) >= 10 ? "#52c41a" : "#faad14",
                },
              }}
            />
          </Card>
        </Col>
      </Row>
      {resourcePrediction.data ? (
        <Card title="Resource prediction (shadow mode)" style={{ marginBottom: 16 }}>
          <Descriptions size="small" bordered column={2}>
            <Descriptions.Item label="Predicted p50">
              {formatJobDuration(resourcePrediction.data.predicted_p50_seconds)}
            </Descriptions.Item>
            <Descriptions.Item label="Predicted p90">
              {formatJobDuration(resourcePrediction.data.predicted_p90_seconds)}
            </Descriptions.Item>
            <Descriptions.Item label="Recommended CPU">
              {resourcePrediction.data.recommended_cpu_cores.toFixed(1)} cores
            </Descriptions.Item>
            <Descriptions.Item label="Recommended memory">
              {resourcePrediction.data.recommended_memory_gb.toFixed(1)} GB
            </Descriptions.Item>
            <Descriptions.Item label="Confidence">
              {Math.round(resourcePrediction.data.confidence * 100)}%
            </Descriptions.Item>
            <Descriptions.Item label="Samples">{resourcePrediction.data.samples}</Descriptions.Item>
          </Descriptions>
        </Card>
      ) : null}

      <Card title="Job Information" style={{ marginBottom: 16 }}>
        <Descriptions column={2} bordered>
          <Descriptions.Item label="Table">
            <Button
              type="link"
              onClick={() => navigate(`/tables/${data.table_id}`)}
              style={{ padding: 0 }}
            >
              {data.table_name}
            </Button>
          </Descriptions.Item>
          <Descriptions.Item label="Operation Type">
            <Tag color="blue">{data.operation_type}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="Engine">
            <Tag color={engineTagColor(data.engine)}>{formatEngineLabel(data.engine)}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="Replica">
            {data.replica || "—"}
          </Descriptions.Item>
          <Descriptions.Item label="Started At">
            {data.started_at
              ? new Date(data.started_at).toLocaleString()
              : "—"}
          </Descriptions.Item>
          <Descriptions.Item label="Completed At">
            {data.completed_at
              ? new Date(data.completed_at).toLocaleString()
              : "In progress"}
          </Descriptions.Item>
        </Descriptions>
      </Card>

      {data.partition_predicates && data.partition_predicates.length > 0 && (
        <Card
          title={
            <Space>
              <FilterOutlined />
              Partition Predicates
            </Space>
          }
          style={{ marginBottom: 16 }}
        >
          <Space orientation="vertical" style={{ width: "100%" }}>
            {data.partition_predicates.map((pred, idx) => (
              <Alert
                key={idx}
                title={<Text code>{pred}</Text>}
                type="info"
                showIcon={false}
              />
            ))}
          </Space>
        </Card>
      )}

      {data.sql_statements && data.sql_statements.length > 0 && (
        <Card
          title={
            <Space>
              <CodeOutlined />
              {data.sql_statements.some((sql) => isNativeStatement(sql)) ? "Rendered statements" : "SQL Statements"}
            </Space>
          }
        >
          {data.sql_statements.map((sql, idx) => (
            <Card
              key={idx}
              type="inner"
              title={`Statement ${idx + 1}`}
              style={{ marginBottom: idx < data.sql_statements!.length - 1 ? 16 : 0 }}
            >
              {isNativeStatement(sql) ? (
                <Space direction="vertical" style={{ width: "100%" }}>
                  <Text code type="secondary">
                    {sql}
                  </Text>
                  <Paragraph
                    copyable
                    code
                    style={{
                      fontSize: "11px",
                      backgroundColor: "#f5f5f5",
                      padding: "12px",
                      borderRadius: "4px",
                      whiteSpace: "pre-wrap",
                      margin: 0
                    }}
                  >
                    See raw job record for execute payload details.
                  </Paragraph>
                </Space>
              ) : (
                <Paragraph
                  copyable
                  code
                  style={{
                    fontSize: "11px",
                    backgroundColor: "#f5f5f5",
                    padding: "12px",
                    borderRadius: "4px",
                    whiteSpace: "pre-wrap",
                    margin: 0
                  }}
                >
                  {sql}
                </Paragraph>
              )}
            </Card>
          ))}
        </Card>
      )}

      <Collapse
        style={{ marginTop: 16 }}
        items={[
          {
            key: "raw",
            label: "Raw job record (API)",
            children: rawJob.data ? (
              <pre className="inline-json">{JSON.stringify(rawJob.data, null, 2)}</pre>
            ) : rawJob.isLoading ? (
              <Spin />
            ) : (
              <Alert type="warning" title={(rawJob.error as Error)?.message ?? "Unavailable"} />
            )
          }
        ]}
      />
    </div>
  );
};
