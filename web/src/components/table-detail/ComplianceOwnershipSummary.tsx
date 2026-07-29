import { Settings } from "lucide-react";
import { useTableDetailContext } from "../../pages/table-detail/TableDetailContext";
import { AsyncBoundary } from "../AsyncBoundary";
import { PendingDeletionTag } from "../status";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";

/**
 * Read-only summary of the two low-frequency cards the old sidebar carried on every tab
 * (Compliance & Protection, Ownership & Alerts). Editing lives in TableSettingsSheet, opened
 * from either the header's gear icon or the "Edit" button here -- this card only needs to
 * answer "what's the current state," not host the forms themselves.
 */
export const ComplianceOwnershipSummary = () => {
  const { compliance, complianceNotFound, row, alertSubscriptions, setSettingsOpen } = useTableDetailContext();
  const subscriberCount = alertSubscriptions.data?.length ?? 0;

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle>Compliance &amp; ownership</CardTitle>
        <Button variant="ghost" size="sm" onClick={() => setSettingsOpen(true)}>
          <Settings className="size-3.5" />
          Edit
        </Button>
      </CardHeader>
      <CardContent>
        <AsyncBoundary
          isLoading={compliance.isLoading}
          isError={Boolean(compliance.error) && !complianceNotFound}
          error={compliance.error}
          isEmpty={complianceNotFound || !compliance.data}
          emptyTitle="No compliance data yet"
          emptyDescription="ABAC and deletion-delay details appear after governance policies apply."
        >
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <SummaryStat label="ABAC policies">
              <Badge variant="default">{compliance.data?.abac_policy_count ?? 0}</Badge>
            </SummaryStat>
            <SummaryStat label="Deletion delay">
              <PendingDeletionTag pending={Boolean(compliance.data?.pending_deletion)} />
            </SummaryStat>
            <SummaryStat label="Owner">
              <span className="truncate text-sm">{row?.owner_email || "Unassigned"}</span>
            </SummaryStat>
            <SummaryStat label="Alert subscribers">
              <span className="text-sm">{subscriberCount}</span>
            </SummaryStat>
          </div>
        </AsyncBoundary>
      </CardContent>
    </Card>
  );
};

const SummaryStat = ({ label, children }: { label: string; children: React.ReactNode }) => (
  <div className="space-y-1">
    <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
    {children}
  </div>
);
