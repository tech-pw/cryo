import type { SourceNamespacesResult } from "../api/client";
import { Alert } from "./ui/alert";

export interface NamespaceErrorAlertProps {
  result?: SourceNamespacesResult;
  loading?: boolean;
  fromScannedFallback?: boolean;
}

export const NamespaceErrorAlert = ({
  result,
  loading,
  fromScannedFallback = false
}: NamespaceErrorAlertProps) => {
  if (loading) return null;
  if (!result || result.ok) return null;
  return (
    <Alert
      className="mb-2"
      variant="warning"
      title="Couldn't reach the catalog to list live schemas"
      description={
        <div className="space-y-1">
          <p>{result.error}</p>
          {fromScannedFallback ? (
            <p>
              Showing schemas Cryo has already scanned in this lakehouse. Trigger a full scan first to discover
              the rest.
            </p>
          ) : null}
        </div>
      }
    />
  );
};
