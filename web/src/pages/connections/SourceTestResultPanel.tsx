import { Alert, Typography } from "antd";
import type { SourceTestResult } from "../../api/types";
import { formatSourceTestSuccessMessage } from "./helpers";

interface SourceTestResultPanelProps {
  result: SourceTestResult;
}

export const SourceTestResultPanel = ({ result }: SourceTestResultPanelProps) => {
  if (!result.ok) {
    return (
      <Alert
        className="page-alert"
        type="error"
        showIcon
        title="Couldn't connect"
        description={result.error}
      />
    );
  }
  return (
    <Alert
      className="page-alert"
      type="success"
      showIcon
      title={formatSourceTestSuccessMessage(result.namespaces.length)}
      description={
        result.note ? (
          <Typography.Text type="secondary" style={{ fontSize: 12.5 }}>
            {result.note}
          </Typography.Text>
        ) : undefined
      }
    />
  );
};

export default SourceTestResultPanel;