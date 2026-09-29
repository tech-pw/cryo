import { Link } from "react-router-dom";

import { PageHeader } from "../ui/page-header";
import { useAuth } from "../../lib/auth";
import { BatchPanel } from "./BatchPanel";

export const BatchReviewPage = () => {
  const { subject } = useAuth();
  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Maintenance batch"
        subtitle="Review the exact tables and recipe, then run maintenance."
        rootCrumb={{ label: "Runs", to: "/runs" }}
      />
      <BatchPanel
        identity={{
          installationId: "local",
          principalId: subject || "anonymous",
          tenantId: "local"
        }}
      />
      <p className="text-sm text-muted-foreground">
        After submit, status is on <Link to="/runs?tab=history">Runs</Link>. Accepted work still waits for admission.
      </p>
    </div>
  );
};
