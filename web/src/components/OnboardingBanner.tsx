import { Alert, Button, Space } from "antd";
import { Link } from "react-router-dom";

interface OnboardingBannerProps {
  sourceCount: number;
  scanCount: number;
}

export const OnboardingBanner = ({ sourceCount, scanCount }: OnboardingBannerProps) => {
  if (sourceCount > 0 && scanCount > 0) return null;
  const missingSources = sourceCount === 0;
  const missingScans = scanCount === 0;
  return (
    <Alert
      type="info"
      showIcon
      style={{ marginBottom: 16 }}
      message="Welcome to Cryo"
      description={
        <Space direction="vertical" size={6}>
          <span>
            {missingSources
              ? "Add a source connection to start discovering Iceberg tables."
              : "Run a scan to populate health, cost, and compaction data."}
          </span>
          <Space>
            {missingSources && (
              <Link to="/connections">
                <Button type="primary" size="small">
                  Add source
                </Button>
              </Link>
            )}
            {missingScans && (
              <Link to="/scans">
                <Button size="small">Run scan</Button>
              </Link>
            )}
          </Space>
        </Space>
      }
    />
  );
};