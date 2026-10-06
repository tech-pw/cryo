import { Link } from "react-router-dom";
import { Alert } from "./ui/alert";
import { Button } from "./ui/button";

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
      className="mb-4"
      variant="info"
      title="Welcome to Cryo"
      description={
        <div className="space-y-2">
          <p>
            {missingSources
              ? "Add a source connection to start discovering Iceberg tables."
              : "Run a scan to populate health, cost, and compaction data."}
          </p>
          <div className="flex flex-wrap gap-2">
            {missingSources ? (
              <Link to="/connections">
                <Button variant="primary" size="sm">
                  Add source
                </Button>
              </Link>
            ) : null}
            {missingScans ? (
              <Link to="/scans">
                <Button size="sm">Run scan</Button>
              </Link>
            ) : null}
          </div>
        </div>
      }
    />
  );
};
