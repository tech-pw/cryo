/**
 * Iceberg table property recommendations panel.
 *
 * Tier 1 — metadata-only properties applied via PyIceberg `set_properties`.
 * Tier 2 — recommendations that link into existing layout / bloom / run flows.
 * Tier 3 — read-only or caution banners (gc kill-switch, history expiry, format upgrade).
 *
 * Implementation: Radix primitives + Tailwind only (no Ant Design). See the
 * Cryo frontend revamp plan Part 1F.1 for the tier model rationale.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Settings } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import {
  applyTableProperties,
  approveSmartLayoutRecommendation,
  executeSmartLayoutRecommendation,
  getPropertyRecommendations,
  type PropertyRecommendationRead
} from "../../api/client";
import { useAuth } from "../../lib/auth";
import { useWriteControl } from "../../lib/useWriteControl";
import { useTableDetailContext } from "../../pages/table-detail/TableDetailContext";
import { AsyncBoundary } from "../AsyncBoundary";
import { EmptyState } from "../common";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle
} from "../ui/alert-dialog";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { Skeleton } from "../ui/skeleton";

const tierVariant = {
  tier1: "success",
  tier2: "default",
  tier3: "warning"
} as const;

const tierLabel: Record<PropertyRecommendationRead["tier"], string> = {
  tier1: "Safe",
  tier2: "Rewrite",
  tier3: "Caution"
};

/** Format raw Iceberg byte-count values into readable sizes where it's safe to do so. */
const formatPropertyValue = (value: string | null | undefined) => {
  if (!value) return "not set";
  if (value === "268435456") return "256MB";
  if (value === "536870912") return "512MB";
  return value;
};

interface RiskyConfirmState {
  open: boolean;
  item: PropertyRecommendationRead | null;
}

const PropertyRecommendationsSkeleton = () => (
  <div className="flex flex-col gap-2 p-4" aria-busy="true" aria-label="Loading property recommendations">
    <Skeleton className="h-4 w-40" />
    <Skeleton className="h-10 w-full" />
    <Skeleton className="h-10 w-full" />
    <Skeleton className="h-10 w-full" />
  </div>
);

export const PropertyRecommendations = () => {
  const { tableId, run } = useTableDetailContext();
  const applyAccess = useWriteControl("applyProperty");
  const bloomAccess = useWriteControl("enableBloom");
  const compactionAccess = useWriteControl("runPropertyCompaction");
  const { api } = useAuth();
  const queryClient = useQueryClient();
  const [riskyConfirm, setRiskyConfirm] = useState<RiskyConfirmState>({ open: false, item: null });

  const recommendations = useQuery({
    queryKey: ["property-recommendations", tableId],
    queryFn: () => getPropertyRecommendations(api, tableId),
    enabled: Boolean(tableId)
  });

  const applyMutation = useMutation({
    mutationFn: (item: PropertyRecommendationRead) =>
      applyTableProperties(api, tableId, {
        properties: { [item.property_key]: item.recommended_value ?? "" },
        confirm_risky: item.needs_confirmation
      }),
    onSuccess: (result, item) => {
      toast.success(
        `${item.property_key} set to ${formatPropertyValue(result.applied[item.property_key])}`
      );
      void queryClient.invalidateQueries({ queryKey: ["property-recommendations", tableId] });
      void queryClient.invalidateQueries({ queryKey: ["table", tableId] });
      void queryClient.invalidateQueries({ queryKey: ["table-audit", tableId] });
    },
    onError: (error: Error) => toast.error(error.message)
  });

  const layoutMutation = useMutation({
    mutationFn: async (item: PropertyRecommendationRead) => {
      if (!item.layout_recommendation_id) {
        throw new Error("No layout recommendation id");
      }
      await approveSmartLayoutRecommendation(api, item.layout_recommendation_id);
      return executeSmartLayoutRecommendation(api, item.layout_recommendation_id);
    },
    onSuccess: () => {
      toast.success("Layout recommendation dispatched");
      void queryClient.invalidateQueries({ queryKey: ["property-recommendations", tableId] });
    },
    onError: (error: Error) => toast.error(error.message)
  });

  const confirmApply = (item: PropertyRecommendationRead) => {
    if (item.needs_confirmation) {
      setRiskyConfirm({ open: true, item });
      return;
    }
    applyMutation.mutate(item);
  };

  const onConfirmRisky = () => {
    const item = riskyConfirm.item;
    setRiskyConfirm({ open: false, item: null });
    if (item) {
      applyMutation.mutate(item);
    }
  };

  const actionButton = (item: PropertyRecommendationRead): ReactNode => {
    if (item.action === "apply" && item.recommended_value) {
      return (
        <>
          <Button
            size="sm"
            variant="primary"
            loading={applyMutation.isPending && applyMutation.variables?.property_key === item.property_key}
            disabled={!applyAccess.allowed}
            title={applyAccess.reason ?? undefined}
            onClick={() => {
              if (!applyAccess.allowed) return;
              confirmApply(item);
            }}
          >
            Apply
          </Button>
          {applyAccess.reason ? (
            <span className="mt-1 block max-w-48 text-right text-[11px] text-muted-foreground">{applyAccess.reason}</span>
          ) : null}
        </>
      );
    }
    if (item.action === "link_layout") {
      if (item.layout_recommendation_id) {
        return (
          <Button
            size="sm"
            loading={layoutMutation.isPending && layoutMutation.variables?.property_key === item.property_key}
            onClick={() => layoutMutation.mutate(item)}
          >
            Apply layout
          </Button>
        );
      }
      return (
        <Button size="sm" variant="secondary" asChild>
          <Link to="/smart-maintenance">Smart Maintenance</Link>
        </Button>
      );
    }
    if (item.action === "link_bloom" && item.bloom_filter_column) {
      return (
        <>
          <Button
            size="sm"
            loading={applyMutation.isPending && applyMutation.variables?.property_key === item.property_key}
            disabled={!bloomAccess.allowed}
            title={
              bloomAccess.reason ??
              "Sets the bloom property for new files. Existing files stay unchanged until rewritten."
            }
            onClick={() => {
              if (!bloomAccess.allowed) return;
              applyMutation.mutate({
                ...item,
                recommended_value: "true"
              });
            }}
          >
            Enable bloom{item.bloom_filter_column ? ` on ${item.bloom_filter_column}` : ""}
          </Button>
          {bloomAccess.reason ? (
            <span className="mt-1 block max-w-48 text-right text-[11px] text-muted-foreground">{bloomAccess.reason}</span>
          ) : null}
        </>
      );
    }
    if (item.action === "link_run") {
      return (
        <>
          <Button
            size="sm"
            loading={run.isPending}
            disabled={!compactionAccess.allowed}
            title={compactionAccess.reason ?? undefined}
            onClick={() => {
              if (!compactionAccess.allowed) return;
              run.mutate();
            }}
          >
            Run compaction
          </Button>
          {compactionAccess.reason ? (
            <span className="mt-1 block max-w-48 text-right text-[11px] text-muted-foreground">{compactionAccess.reason}</span>
          ) : null}
        </>
      );
    }
    return null;
  };

  const items = recommendations.data?.items ?? [];
  const gcBlocked = recommendations.data?.gc_blocked ?? false;

  return (
    <Card className="mb-4">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Settings aria-hidden="true" className="size-4" />
          Iceberg property recommendations
        </CardTitle>
      </CardHeader>
      <CardContent>
        <AsyncBoundary
          isLoading={recommendations.isLoading}
          isError={Boolean(recommendations.error)}
          error={recommendations.error}
          onRetry={() => void recommendations.refetch()}
          skeleton={<PropertyRecommendationsSkeleton />}
        >
          {gcBlocked ? (
            <div
              role="alert"
              className="mb-3 flex gap-3 rounded-[var(--radius-default)] border border-[color:var(--color-status-danger)]/30 bg-[color:var(--color-status-danger)]/10 p-3 text-sm"
            >
              <span className="font-medium">Maintenance blocked</span>
              <span className="text-muted-foreground">
                gc.enabled=false —                 Expire snapshots and orphan removal are skipped while gc.enabled is false. Other property writes still apply.
              </span>
            </div>
          ) : null}

          {items.length ? (
            <div className="overflow-x-auto" data-testid="property-recommendations-table">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-xs font-medium text-muted-foreground">
                    <th className="px-3 py-2 font-medium">Property</th>
                    <th className="px-3 py-2 font-medium">Current</th>
                    <th className="px-3 py-2 font-medium">Recommended</th>
                    <th className="px-3 py-2 font-medium">Tier</th>
                    <th className="px-3 py-2 font-medium">Reason</th>
                    <th className="px-3 py-2 font-medium text-right">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr key={item.property_key} className="border-b border-border last:border-b-0">
                      <td className="max-w-[240px] truncate px-3 py-2 font-mono text-xs" title={item.property_key}>
                        {item.property_key}
                      </td>
                      <td className="px-3 py-2 font-mono text-xs">
                        {formatPropertyValue(item.current_value)}
                      </td>
                      <td className="px-3 py-2 font-mono text-xs">
                        {formatPropertyValue(item.recommended_value)}
                      </td>
                      <td className="px-3 py-2">
                        <Badge variant={tierVariant[item.tier]}>{tierLabel[item.tier]}</Badge>
                      </td>
                      <td className="max-w-[420px] truncate px-3 py-2 text-xs text-muted-foreground" title={item.reason}>
                        {item.reason}
                      </td>
                      <td className="px-3 py-2 text-right">{actionButton(item)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState
              title="No property recommendations"
              description="Scanned properties match Cryo's guidance for this table."
            />
          )}
        </AsyncBoundary>
      </CardContent>

      <AlertDialog
        open={riskyConfirm.open}
        onOpenChange={(open) =>
          setRiskyConfirm((prev) => ({ open, item: open ? prev.item : null }))
        }
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Set {riskyConfirm.item?.property_key ?? "this property"} to {riskyConfirm.item?.recommended_value ?? "the recommended value"}?
            </AlertDialogTitle>
            <AlertDialogDescription>
              {riskyConfirm.item?.reason ??
                "This writes a catalog property. It does not rewrite existing files."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={onConfirmRisky} disabled={!applyAccess.allowed} title={applyAccess.reason ?? undefined}>
              Apply
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
};