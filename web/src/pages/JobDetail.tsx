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
import { useParams, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";

const { Title, Text, Paragraph } = Typography;

interface JobDetail {
  job_id: string;
  table_id: string;
  table_name: string;
  operation_type: string;
  status: string;
  engine: string;
  replica: string | null;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  input_files_count: number | null;
  output_files_count: number | null;
  effectiveness_ratio: number | null;
  error_message: string | null;
  sql_statements: string[] | null;
  partition_predicates: string[] | null;
}

const statusConfig: Record<string, { icon: any; color: string }> = {
  succeeded: { icon: <CheckCircleOutlined />, color: "success" },
  failed: { icon: <CloseCircleOutlined />, color: "error" },
  running: { icon: <ClockCircleOutlined />, color: "processing" },
  queued: { icon: <ClockCircleOutlined />, color: "default" },
};

const formatDuration = (seconds: number | null) => {
  if (!seconds) return "—";
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  if (seconds < 3600) return `${(seconds / 60).toFixed(1)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
};

export const JobDetail = () => {
  const { jobId } = useParams<{ jobId: string }>();
  const navigate = useNavigate();
  const { token } = useAuth();

  const { data, isLoading, error } = useQuery<JobDetail>({
    queryKey: ["job-detail", jobId],
    queryFn: async () => {
      const response = await fetch(`/api/jobs/${jobId}/detail`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) throw new Error("Failed to fetch job detail");
      return response.json();
    },
    enabled: !!jobId,
  });

  if (isLoading) {
    return (
      <div style={{ textAlign: "center", padding: "60px" }}>
        <Spin size="large" />
      </div>
    );
  }

  if (error || !data) {
    return (
      <Alert
        message="Error"
        description="Failed to load job details"
        type="error"
        showIcon
      />
    );
  }

  const statusInfo = statusConfig[data.status] || statusConfig.queued;

  return (
    <div>
      <div className="page-header">
        <div>
          <Button
            icon={<ArrowLeftOutlined />}
            onClick={() => navigate("/runs")}
            style={{ marginBottom: 16 }}
          >
            Back to Runs
          </Button>
          <Title level={2}>
            <DatabaseOutlined /> Maintenance Job Detail
          </Title>
          <Text type="secondary">Job ID: {data.job_id}</Text>
        </div>
        <Tag
          icon={statusInfo.icon}
          color={statusInfo.color}
          style={{ fontSize: "14px", padding: "6px 12px" }}
        >
          {data.status.toUpperCase()}
        </Tag>
      </div>

      {data.error_message && (
        <Alert
          message="Job Failed"
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
              value={formatDuration(data.duration_seconds)}
              prefix={<ClockCircleOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="Input Files"
              value={data.input_files_count || 0}
              valueStyle={{ color: "#1890ff" }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="Output Files"
              value={data.output_files_count || 0}
              valueStyle={{ color: "#52c41a" }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="Effectiveness Ratio"
              value={data.effectiveness_ratio?.toFixed(1) || "—"}
              suffix=":1"
              valueStyle={{
                color: (data.effectiveness_ratio || 0) >= 10 ? "#52c41a" : "#faad14",
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
          <Space direction="vertical" style={{ width: "100%" }}>
            {data.partition_predicates.map((pred, idx) => (
              <Alert
                key={idx}
                message={<Text code>{pred}</Text>}
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
    </div>
  );
};
