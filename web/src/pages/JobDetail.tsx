import { useQuery } from "@tanstack/react-query";
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
} from "@ant-design/icons";
import { Link, useParams, useNavigate } from "react-router-dom";
import { getJob, getJobDetailDashboard, type JobDetailDashboard } from "../api/client";
import { PageHeader, QueryErrorAlert } from "../components/common";
import { StatusTag } from "../components/status";
import { useAuth } from "../lib/auth";
import { formatDuration } from "../lib/format";

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
            <Tag color="green">{data.engine.toUpperCase()}</Tag>
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
              SQL Statements
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
              <Paragraph
                copyable
                code
                style={{
                  fontSize: "11px",
                  backgroundColor: "#f5f5f5",
                  padding: "12px",
                  borderRadius: "4px",
                  whiteSpace: "pre-wrap",
                  margin: 0,
                }}
              >
                {sql}
              </Paragraph>
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
