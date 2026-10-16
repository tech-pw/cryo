import { Cloud, Database, GitBranch, Network, Plus, Star, Trash2 } from "lucide-react";
import type { ReactNode } from "react";
import { patchSource } from "../../api/client";
import { AwsStatusBadge } from "../../components/AwsStatusBadge";
import { QueryErrorAlert } from "../../components/common";
import { ConfirmAction } from "../../components/confirm";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { isConnectionPendingScan } from "../../lib/connectionScanPrompt";
import { useAuth } from "../../lib/auth";
import { VIEWER_REASON, writeAccess } from "../../lib/permissions";
import { ConnectionList, ConnectionRow, ConnectionSection, ConnectionStatus } from "./ConnectionList";
import { useConnectionsContext } from "./ConnectionsContext";
import { catalogTypeLabel, sourceDetails } from "./connectionSummary";

const CATALOG_ICONS: Record<string, ReactNode> = {
  rest: <Network />,
  hive: <Database />,
  glue: <Cloud />,
  polaris: <Star />,
  nessie: <GitBranch />
};

export const LakehousesTab = () => {
  const {
    api,
    sources,
    awsStatuses,
    pendingScanVersion,
    setSourceDrawer,
    setLatencySource,
    purgeSourceMutation,
    queryClient
  } = useConnectionsContext();
  const { role } = useAuth();
  const createAccess = writeAccess(role, "createSource");
  const patchAccess = writeAccess(role, "patchSource");
  const purgeAccess = writeAccess(role, "purgeSource");

  const newButton = (
    <Button
      variant="primary"
      disabled={!createAccess.allowed}
      title={createAccess.reason ?? undefined}
      onClick={() => setSourceDrawer("new")}
    >
      <Plus aria-hidden="true" />
      New lakehouse
    </Button>
  );

  return (
    <ConnectionSection
      title="Lakehouses"
      description="Iceberg catalogs Cryo reads to discover tables and scan health. Read-only: a lakehouse never runs maintenance."
      note={role === "viewer" ? VIEWER_REASON : undefined}
      action={newButton}
    >
      {sources.isError ? (
        <QueryErrorAlert error={sources.error} title="Failed to load lakehouses" />
      ) : (
        <ConnectionList
          key={`lakehouses-${pendingScanVersion}`}
          label="Lakehouses"
          loading={sources.isLoading}
          emptyTitle="No lakehouses connected"
          emptyDescription="Connect a REST, Hive Metastore, or Glue catalog. Cryo scans it and shows table health here."
        >
          {(sources.data ?? []).map((source) => (
            <ConnectionRow
              key={source.id ?? `source-${source.type}-${source.name}`}
              icon={CATALOG_ICONS[source.type] ?? <Database />}
              name={source.name}
              kind={catalogTypeLabel(source.type)}
              enabled={source.enabled}
              status={<ConnectionStatus enabled={source.enabled} />}
              badges={
                <>
                  <AwsStatusBadge status={awsStatuses[source.id]} />
                  {isConnectionPendingScan("lakehouse", source.id) ? (
                    <Badge variant="warning">Scan required</Badge>
                  ) : null}
                </>
              }
              details={sourceDetails(source)}
              actions={
                <>
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={!patchAccess.allowed}
                    title={patchAccess.reason ?? undefined}
                    onClick={() => setSourceDrawer(source)}
                  >
                    Edit
                  </Button>
                  <Button size="sm" variant="secondary" onClick={() => setLatencySource(source)}>
                    Latency
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={!patchAccess.allowed}
                    title={patchAccess.reason ?? undefined}
                    onClick={() =>
                      patchSource(api, source.id, { enabled: !source.enabled }).then(() =>
                        queryClient.invalidateQueries({ queryKey: ["sources"] })
                      )
                    }
                  >
                    {source.enabled ? "Disable" : "Enable"}
                  </Button>
                  {purgeAccess.hidden ? null : (
                    <ConfirmAction
                      title={`Permanently delete "${source.name}"?`}
                      description="Deletes the lakehouse, its scanned tables, and the stored AWS integration. If jobs still reference it, nothing is deleted. Use Disable to pause it instead."
                      onConfirm={() => purgeSourceMutation.mutate(source.id)}
                    >
                      <Button
                        size="sm"
                        variant="ghost"
                        className="connection-delete px-2"
                        aria-label="Delete"
                        title={`Delete ${source.name}`}
                        loading={purgeSourceMutation.isPending}
                      >
                        {purgeSourceMutation.isPending ? null : <Trash2 aria-hidden="true" />}
                      </Button>
                    </ConfirmAction>
                  )}
                </>
              }
            />
          ))}
        </ConnectionList>
      )}
    </ConnectionSection>
  );
};
