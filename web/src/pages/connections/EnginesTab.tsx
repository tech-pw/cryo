import { useQuery } from "@tanstack/react-query";
import { Cpu, Flame, Plus, Server, Trash2, Waves } from "lucide-react";
import type { ReactNode } from "react";
import { listPresets, patchDestination } from "../../api/client";
import type { DestinationRead } from "../../api/types";
import { ConfirmAction } from "../../components/confirm";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import {
  formatEngineLabel,
  nativeEngineOptionLabel,
  nativeFleetDisabledRowLabel,
  resolveNativeEngineImpl
} from "../../lib/engines";
import { isConnectionPendingScan } from "../../lib/connectionScanPrompt";
import { getRuntimeFlags } from "../../lib/runtimeFlags";
import { useAuth } from "../../lib/auth";
import { VIEWER_REASON, writeAccess } from "../../lib/permissions";
import { ConnectionList, ConnectionRow, ConnectionSection, ConnectionStatus } from "./ConnectionList";
import { useConnectionsContext } from "./ConnectionsContext";
import { cryoEngineCaveat, destinationDetails, isCryoEngine } from "./connectionSummary";

const ENGINE_ICONS: Record<string, ReactNode> = {
  trino: <Server />,
  spark_emr: <Flame />,
  native: <Cpu />,
  flink: <Waves />
};

export const EnginesTab = () => {
  const {
    api,
    destinations,
    pendingScanVersion,
    setDestinationDrawer,
    probe,
    purgeDestinationMutation,
    queryClient
  } = useConnectionsContext();
  const { role, token } = useAuth();
  const createAccess = writeAccess(role, "createDestination");
  const patchAccess = writeAccess(role, "patchDestination");
  const purgeAccess = writeAccess(role, "purgeDestination");
  const testAccess = writeAccess(role, "testConnection");

  const runtime = useQuery({
    queryKey: ["runtime-flags", api.token],
    queryFn: () => getRuntimeFlags(api)
  });
  const presets = useQuery({
    queryKey: ["presets", token],
    queryFn: () => listPresets(api),
    staleTime: 60_000
  });
  const nativeEnabled = runtime.data?.native_engine_enabled;
  const nativeFallback = runtime.data?.native_engine_fallback;
  const defaultPresetTarget = presets.data?.find((preset) => preset.is_default && preset.enabled)?.destination_id;

  const all = destinations.data ?? [];
  const cryoEngines = all.filter(isCryoEngine);
  const userEngines = all.filter((destination) => !isCryoEngine(destination));

  const engineLabel = (destination: DestinationRead) =>
    destination.engine === "native" && nativeEnabled !== undefined
      ? nativeEngineOptionLabel(nativeEnabled, resolveNativeEngineImpl(destination.config))
      : formatEngineLabel(destination.engine, destination.config);

  const statusOf = (destination: DestinationRead) => {
    if (destination.engine === "native" && nativeEnabled === false && nativeFallback) {
      return <ConnectionStatus enabled={false} label={nativeFleetDisabledRowLabel(nativeFallback)} />;
    }
    return <ConnectionStatus enabled={Boolean(destination.enabled)} />;
  };

  const toggleEnabled = (destination: DestinationRead) =>
    patchDestination(api, destination.id, { enabled: !destination.enabled }).then(() =>
      queryClient.invalidateQueries({ queryKey: ["destinations"] })
    );

  const renderRow = (destination: DestinationRead) => {
    const managed = isCryoEngine(destination);
    const editButton = (
      <Button
        size="sm"
        variant="secondary"
        disabled={!patchAccess.allowed}
        title={patchAccess.reason ?? undefined}
        onClick={managed ? undefined : () => setDestinationDrawer(destination)}
      >
        Edit
      </Button>
    );
    const toggleButton = (
      <Button
        size="sm"
        variant="ghost"
        disabled={!patchAccess.allowed}
        title={patchAccess.reason ?? undefined}
        onClick={managed && destination.enabled ? undefined : () => toggleEnabled(destination)}
      >
        {destination.enabled ? "Disable" : "Enable"}
      </Button>
    );
    return (
      <ConnectionRow
        key={destination.id ?? `destination-${destination.engine}-${destination.name}`}
        icon={ENGINE_ICONS[destination.engine] ?? <Cpu />}
        name={destination.name}
        kind={engineLabel(destination)}
        enabled={Boolean(destination.enabled)}
        status={statusOf(destination)}
        badges={
          <>
            {defaultPresetTarget && defaultPresetTarget === destination.id ? (
              <Badge variant="default">Default preset</Badge>
            ) : null}
            {isConnectionPendingScan("engine", destination.id) ? (
              <Badge variant="warning">Scan lakehouse first</Badge>
            ) : null}
          </>
        }
        details={destinationDetails(destination)}
        actions={
          <>
            {managed ? (
              <ConfirmAction
                title={`Edit ${destination.name}, a Cryo engine?`}
                description={`${cryoEngineCaveat(destination)} Your edits persist across Cryo upgrades.`}
                okText="Edit anyway"
                disabled={!patchAccess.allowed}
                onConfirm={() => setDestinationDrawer(destination)}
              >
                {editButton}
              </ConfirmAction>
            ) : (
              editButton
            )}
            <Button
              size="sm"
              variant="secondary"
              loading={probe.isPending}
              disabled={!testAccess.allowed}
              title={testAccess.reason ?? undefined}
              onClick={() => probe.mutate(destination)}
            >
              Test
            </Button>
            {managed && destination.enabled ? (
              <ConfirmAction
                title={`Disable ${destination.name}?`}
                description={cryoEngineCaveat(destination)}
                okText="Disable"
                okButtonProps={{ danger: true }}
                disabled={!patchAccess.allowed}
                onConfirm={() => toggleEnabled(destination)}
              >
                {toggleButton}
              </ConfirmAction>
            ) : (
              toggleButton
            )}
            {purgeAccess.hidden ? null : (
              <ConfirmAction
                title={`Permanently delete "${destination.name}"?`}
                description={`${
                  managed ? "Cryo does not re-create its engines after a delete; add one back manually if you need it. " : ""
                }This cannot be undone. If any presets or maintenance runs still reference this engine, deletion will fail until those are reassigned or removed. If you just want to pause it, use Disable instead.`}
                onConfirm={() => purgeDestinationMutation.mutate(destination.id)}
              >
                <Button
                  size="sm"
                  variant="ghost"
                  className="connection-delete px-2"
                  aria-label="Delete"
                  title={`Delete ${destination.name}`}
                  loading={purgeDestinationMutation.isPending}
                >
                  {purgeDestinationMutation.isPending ? null : <Trash2 aria-hidden="true" />}
                </Button>
              </ConfirmAction>
            )}
          </>
        }
      />
    );
  };

  return (
    <div className="flex flex-col gap-8">
      <ConnectionSection
        title="Cryo engines"
        description="Shipped with Cryo and run inside this deployment: the native iceberg-go engine and Spark on Kubernetes."
        note={
          role === "viewer"
            ? VIEWER_REASON
            : "Editable, but other work depends on them. Cryo asks you to confirm before you edit or disable one."
        }
      >
        <ConnectionList
          key={`cryo-engines-${pendingScanVersion}`}
          label="Cryo engines"
          loading={destinations.isLoading}
          emptyTitle="No Cryo engines provisioned"
          emptyDescription="Migrations seed the native and Spark-on-Kubernetes engines. If they are missing, this install was set up without them or they were deleted."
        >
          {cryoEngines.map(renderRow)}
        </ConnectionList>
      </ConnectionSection>
      <ConnectionSection
        title="Your engines"
        description="Trino, EMR Serverless, or Flink clusters you connected. Presets and manual runs can target any of them."
        action={
          <Button
            variant="primary"
            disabled={!createAccess.allowed}
            title={createAccess.reason ?? undefined}
            onClick={() => setDestinationDrawer("new")}
          >
            <Plus aria-hidden="true" />
            New engine
          </Button>
        }
      >
        <ConnectionList
          key={`user-engines-${pendingScanVersion}`}
          label="Your engines"
          loading={destinations.isLoading}
          emptyTitle="No engines added yet"
          emptyDescription="Connect a Trino cluster or EMR Serverless application to run maintenance on your own compute."
        >
          {userEngines.map(renderRow)}
        </ConnectionList>
      </ConnectionSection>
    </div>
  );
};
