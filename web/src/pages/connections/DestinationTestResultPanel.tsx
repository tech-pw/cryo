import { Alert } from "../../components/ui/alert";
import { formatDestinationTestSummary, type DestinationCapacityTestResult } from "./helpers";

interface DestinationTestResultPanelProps {
  result: DestinationCapacityTestResult;
  onDismiss: () => void;
}

/**
 * Human-readable summary of an engine capacity check (`POST /destinations/{id}/test`), mirroring
 * SourceTestResultPanel's pattern. Replaces the previous raw-JSON dump so operators see a plain-
 * English "is this engine ready?" answer, with the exact API response available on demand for
 * troubleshooting rather than always shown.
 */
export const DestinationTestResultPanel = ({ result, onDismiss }: DestinationTestResultPanelProps) => (
  <Alert
    className="page-alert"
    variant={result.ok && typeof result.available === "number" ? "success" : "warning"}
    title={
      result.ok && typeof result.available === "number"
        ? "Engine capacity check passed"
        : "Engine was not probed"
    }
    description={
      <div className="mt-1 space-y-2">
        <p>{formatDestinationTestSummary(result)}</p>
        {result.message ? <p className="text-xs text-muted-foreground">{result.message}</p> : null}
        <details>
          <summary className="cursor-pointer text-xs text-muted-foreground">Technical details</summary>
          <pre className="inline-json mt-1 overflow-x-auto text-xs">{JSON.stringify(result, null, 2)}</pre>
        </details>
      </div>
    }
    onDismiss={onDismiss}
  />
);

export default DestinationTestResultPanel;
