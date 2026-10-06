import type { SourceTestResult } from "../../api/types";
import { Alert } from "../../components/ui/alert";
import { formatSourceTestSuccessMessage } from "./helpers";

interface SourceTestResultPanelProps {
  result: SourceTestResult;
}

export const SourceTestResultPanel = ({ result }: SourceTestResultPanelProps) => {
  if (!result.ok) {
    return (
      <Alert
        className="page-alert"
        variant="error"
        title="Couldn't connect"
        description={result.error}
      />
    );
  }
  return (
    <Alert
      className="page-alert"
      variant="success"
      title={formatSourceTestSuccessMessage(result.namespaces.length)}
      description={
        result.note ? <span className="text-xs text-muted-foreground">{result.note}</span> : undefined
      }
    />
  );
};

export default SourceTestResultPanel;
