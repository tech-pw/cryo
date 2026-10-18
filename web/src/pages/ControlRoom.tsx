import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef, RowSelectionState } from "@tanstack/react-table";
import {
  Activity,
  CirclePause,
  Fingerprint,
  ListChecks,
  LockKeyhole,
  OctagonX,
  RadioTower,
  RefreshCw,
  ShieldCheck,
  TriangleAlert
} from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import {
  getMaintenanceSafetySummary,
  listDestinations,
  listMaintenanceControls,
  listMaintenanceEffects,
  listMaintenanceIntents,
  pauseMaintenance,
  resolveMaintenanceEffect,
  resolveMaintenanceEffectsBatch,
  resumeMaintenance
} from "../api/client";
import type {
  EffectReceiptRead,
  MaintenanceControlRead,
  MaintenanceIntentRead,
  MaintenanceSafetySummary
} from "../api/types";
import { AsyncBoundary } from "../components/AsyncBoundary";
import { PageHeader, QueryErrorAlert } from "../components/common";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger
} from "../components/ui/alert-dialog";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { DataTable } from "../components/ui/data-table";
import { Input } from "../components/ui/input";
import { Skeleton } from "../components/ui/skeleton";
import { useAuth } from "../lib/auth";
import { cn } from "../lib/cn";
import { formatTime } from "../lib/format";

const CONTROL_ROOM_KEY = ["maintenance-safety"] as const;

const shortId = (value: string) => value.slice(0, 8);

const stateVariant = (state: string): "success" | "warning" | "danger" | "secondary" => {
  if (state === "succeeded" || state === "ready") return "success";
  if (state === "unknown" || state === "reconcile_required" || state === "submitted") return "warning";
  if (state === "failed" || state === "expired" || state === "cancelled") return "danger";
  return "secondary";
};

const StateBadge = ({ state }: { state: string }) => (
  <Badge variant={stateVariant(state)} className="capitalize">
    {state.replaceAll("_", " ")}
  </Badge>
);

interface SafetyPosture {
  label: string;
  detail: string;
  tone: "clear" | "contained" | "held";
}

const safetyPosture = (summary: MaintenanceSafetySummary): SafetyPosture => {
  if (summary.active_pauses > 0) {
    return {
      label: "Maintenance held",
      detail: "One or more safety scopes are paused. New effects are blocked before submission.",
      tone: "held"
    };
  }
  if (summary.ambiguous_effects > 0) {
    return {
      label: "Contained uncertainty",
      detail: `${summary.ambiguous_effects} effect${summary.ambiguous_effects === 1 ? "" : "s"} cannot be replayed until reconciliation proves the outcome.`,
      tone: "contained"
    };
  }
  return {
    label: "Execution clear",
    detail: "No active holds or ambiguous external effects require operator action.",
    tone: "clear"
  };
};

const postureStyles: Record<SafetyPosture["tone"], string> = {
  clear: "bg-[color-mix(in_srgb,var(--color-status-success)_9%,var(--color-panel))]",
  contained: "bg-[color-mix(in_srgb,var(--color-status-warning)_10%,var(--color-panel))]",
  held: "bg-[color-mix(in_srgb,var(--color-status-danger)_10%,var(--color-panel))]"
};

const postureIconStyles: Record<SafetyPosture["tone"], string> = {
  clear: "bg-[var(--color-status-success)] text-background",
  contained: "bg-[var(--color-status-warning)] text-background",
  held: "bg-[var(--color-status-danger)] text-background"
};

const SafetyRail = ({
  summary,
  actions
}: {
  summary: MaintenanceSafetySummary;
  actions: React.ReactNode;
}) => {
  const posture = safetyPosture(summary);
  const Icon = posture.tone === "clear" ? ShieldCheck : posture.tone === "held" ? CirclePause : TriangleAlert;
  return (
    <section
      className={cn(
        "mb-5 overflow-hidden rounded-[var(--radius-default)] border border-border",
        postureStyles[posture.tone]
      )}
      aria-label={`Safety posture: ${posture.label}`}
    >
      <div className="grid gap-0 lg:grid-cols-[minmax(18rem,1.35fr)_repeat(3,minmax(8rem,0.45fr))_auto]">
        <div className="flex min-w-0 items-center gap-3 px-4 py-4">
          <span className={cn("grid size-9 shrink-0 place-items-center rounded-full", postureIconStyles[posture.tone])}>
            <Icon className="size-4" aria-hidden />
          </span>
          <div className="min-w-0">
            <p className="text-base font-semibold tracking-[-0.01em] text-foreground">{posture.label}</p>
            <p className="mt-0.5 max-w-[62ch] text-xs leading-relaxed text-muted-foreground">{posture.detail}</p>
          </div>
        </div>
        {[
          ["Ambiguous effects", summary.ambiguous_effects],
          ["Active attempts", summary.active_attempts],
          ["Active holds", summary.active_pauses]
        ].map(([label, value]) => (
          <div
            key={label}
            className="flex flex-col justify-center border-t border-border/70 px-4 py-3 lg:border-l lg:border-t-0"
          >
            <p className="text-[11px] uppercase tracking-[0.08em] text-muted-foreground">{label}</p>
            <p className="mt-1 text-xl font-semibold tabular-nums text-foreground">{value}</p>
          </div>
        ))}
        <div className="flex items-center border-t border-border/70 px-4 py-3 lg:border-l lg:border-t-0">{actions}</div>
      </div>
    </section>
  );
};

const EmergencyStop = ({
  disabled,
  pending,
  onConfirm
}: {
  disabled: boolean;
  pending: boolean;
  onConfirm: (reason: string) => void;
}) => {
  const [reason, setReason] = useState("");
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button variant="danger" disabled={disabled}>
          <OctagonX className="size-4" aria-hidden />
          Hold all maintenance
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Hold all maintenance?</AlertDialogTitle>
          <AlertDialogDescription>
            Cryo will block new effect submissions globally. Engine statements already submitted may continue and will
            still require reconciliation.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div>
          <label htmlFor="emergency-stop-reason" className="mb-1.5 block text-sm font-medium text-foreground">
            Operator reason
          </label>
          <Input
            id="emergency-stop-reason"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="Incident, change window, or containment reason"
            autoComplete="off"
          />
        </div>
        <AlertDialogFooter>
          <AlertDialogCancel>Keep running</AlertDialogCancel>
          <AlertDialogAction
            className="bg-destructive text-destructive-foreground hover:opacity-90"
            disabled={reason.trim().length < 3 || pending}
            onClick={() => onConfirm(reason.trim())}
          >
            Hold all maintenance
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
};

const ResumeControl = ({
  control,
  disabled,
  pending,
  onConfirm
}: {
  control: MaintenanceControlRead;
  disabled: boolean;
  pending: boolean;
  onConfirm: (control: MaintenanceControlRead, reason: string) => void;
}) => {
  const [reason, setReason] = useState("");
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button size="sm" variant="secondary" disabled={disabled}>
          Resume
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Resume this maintenance scope?</AlertDialogTitle>
          <AlertDialogDescription>
            This advances the control generation for <strong>{control.scope_type}:{control.scope_id}</strong>. Eligible
            work may be admitted on the next Governor cycle.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div>
          <label htmlFor={`resume-reason-${control.scope_id}`} className="mb-1.5 block text-sm font-medium text-foreground">
            Verification note
          </label>
          <Input
            id={`resume-reason-${control.scope_id}`}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="What was checked before resuming?"
            autoComplete="off"
          />
        </div>
        <AlertDialogFooter>
          <AlertDialogCancel>Leave paused</AlertDialogCancel>
          <AlertDialogAction
            disabled={reason.trim().length < 3 || pending}
            onClick={() => onConfirm(control, reason.trim())}
          >
            Resume maintenance
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
};

type HoldTarget = { id: string; label: string };

const TargetedHold = ({
  targets,
  disabled,
  pending,
  onConfirm
}: {
  targets: HoldTarget[];
  disabled: boolean;
  pending: boolean;
  onConfirm: (destinationId: string, reason: string) => void;
}) => {
  const [chosenId, setDestinationId] = useState("");
  const destinationId = targets.some((target) => target.id === chosenId) ? chosenId : (targets[0]?.id ?? "");
  const [reason, setReason] = useState("");
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button variant="secondary" disabled={disabled || targets.length === 0}>
          <LockKeyhole className="size-4" aria-hidden />
          Hold destination
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Hold one execution destination</AlertDialogTitle>
          <AlertDialogDescription>
            New effects for this destination stop at both admission and the final pre-submit guard.
            Other destinations keep running.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="space-y-3">
          <div>
            <label htmlFor="target-hold-destination" className="mb-1.5 block text-sm font-medium text-foreground">
              Destination
            </label>
            <select
              id="target-hold-destination"
              className="h-9 w-full rounded-[var(--radius-default)] border border-border bg-background px-3 text-sm text-foreground"
              value={destinationId}
              onChange={(event) => setDestinationId(event.target.value)}
            >
              {targets.map((target) => (
                <option key={target.id} value={target.id}>
                  {target.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="target-hold-reason" className="mb-1.5 block text-sm font-medium text-foreground">
              Containment reason
            </label>
            <Input
              id="target-hold-reason"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="Incident or cohort regression"
              autoComplete="off"
            />
          </div>
        </div>
        <AlertDialogFooter>
          <AlertDialogCancel>Keep running</AlertDialogCancel>
          <AlertDialogAction
            disabled={!destinationId || reason.trim().length < 3 || pending}
            onClick={() => onConfirm(destinationId, reason.trim())}
          >
            Hold destination
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
};

const ResolveEffectsBatch = ({
  count,
  disabled,
  pending,
  onConfirm
}: {
  count: number;
  disabled: boolean;
  pending: boolean;
  onConfirm: (outcome: "succeeded" | "failed", reason: string, continueJob: boolean) => void;
}) => {
  const [outcome, setOutcome] = useState<"succeeded" | "failed">("succeeded");
  const [reason, setReason] = useState("");
  const [continueJob, setContinueJob] = useState(true);
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button size="sm" variant="secondary" disabled={disabled || count === 0}>
          <ListChecks className="size-4" aria-hidden />
          Attest selected ({count})
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Record one proven outcome for {count} effects</AlertDialogTitle>
          <AlertDialogDescription>
            Apply this only when the same engine evidence proves every selected receipt. Each result
            remains individually audited; conflicts stay unresolved.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="grid grid-cols-2 gap-2" role="group" aria-label="Batch proven outcome">
          <Button
            type="button"
            variant={outcome === "succeeded" ? "primary" : "secondary"}
            aria-pressed={outcome === "succeeded"}
            onClick={() => setOutcome("succeeded")}
          >
            All succeeded
          </Button>
          <Button
            type="button"
            variant={outcome === "failed" ? "danger" : "secondary"}
            aria-pressed={outcome === "failed"}
            onClick={() => setOutcome("failed")}
          >
            All failed
          </Button>
        </div>
        <div>
          <label htmlFor="batch-effect-resolution" className="mb-1.5 block text-sm font-medium text-foreground">
            Shared evidence and verification
          </label>
          <Input
            id="batch-effect-resolution"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="Engine history, incident record, or query evidence"
            autoComplete="off"
          />
        </div>
        <label className="flex items-start gap-2 rounded-[var(--radius-sm)] bg-[var(--color-surface-sunken)] p-3 text-sm">
          <input
            type="checkbox"
            className="mt-0.5 size-4 accent-[var(--color-primary)]"
            checked={continueJob}
            onChange={(event) => setContinueJob(event.target.checked)}
          />
          <span>
            <span className="block font-medium text-foreground">Return remaining work to admission</span>
            <span className="mt-0.5 block text-xs leading-relaxed text-muted-foreground">
              Each intent still passes holds, expiry, load, and fencing checks independently.
            </span>
          </span>
        </label>
        <AlertDialogFooter>
          <AlertDialogCancel>Keep unresolved</AlertDialogCancel>
          <AlertDialogAction
            disabled={reason.trim().length < 3 || pending}
            onClick={() => onConfirm(outcome, reason.trim(), continueJob)}
          >
            Record {count} outcomes
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
};

const ResolveEffect = ({
  effect,
  disabled,
  pending,
  onConfirm
}: {
  effect: EffectReceiptRead;
  disabled: boolean;
  pending: boolean;
  onConfirm: (outcome: "succeeded" | "failed", reason: string, continueJob: boolean) => void;
}) => {
  const [outcome, setOutcome] = useState<"succeeded" | "failed">("succeeded");
  const [reason, setReason] = useState("");
  const [continueJob, setContinueJob] = useState(true);
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button size="sm" variant="secondary" disabled={disabled}>
          Resolve
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Record the proven engine outcome</AlertDialogTitle>
          <AlertDialogDescription>
            This is an operator attestation for effect <span className="font-mono">{shortId(effect.id)}</span>. Marking it
            succeeded prevents replay; marking it failed permits a new attempt with the same stable receipt.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="grid grid-cols-2 gap-2" role="group" aria-label="Proven outcome">
          <Button
            type="button"
            variant={outcome === "succeeded" ? "primary" : "secondary"}
            aria-pressed={outcome === "succeeded"}
            onClick={() => setOutcome("succeeded")}
          >
            Effect succeeded
          </Button>
          <Button
            type="button"
            variant={outcome === "failed" ? "danger" : "secondary"}
            aria-pressed={outcome === "failed"}
            onClick={() => setOutcome("failed")}
          >
            Effect failed
          </Button>
        </div>
        <div>
          <label htmlFor={`effect-resolution-${effect.id}`} className="mb-1.5 block text-sm font-medium text-foreground">
            Evidence and verification
          </label>
          <Input
            id={`effect-resolution-${effect.id}`}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="Where was the outcome verified?"
            autoComplete="off"
          />
          {effect.external_reference ? (
            <p className="mt-1.5 text-xs text-muted-foreground">
              Submission reference: <span className="font-mono text-foreground">{effect.external_reference}</span>
            </p>
          ) : null}
        </div>
        <label className="flex items-start gap-2 rounded-[var(--radius-sm)] bg-[var(--color-surface-sunken)] p-3 text-sm">
          <input
            type="checkbox"
            className="mt-0.5 size-4 accent-[var(--color-primary)]"
            checked={continueJob}
            onChange={(event) => setContinueJob(event.target.checked)}
          />
          <span>
            <span className="block font-medium text-foreground">Return remaining work to admission</span>
            <span className="mt-0.5 block text-xs leading-relaxed text-muted-foreground">
              The Governor will re-check holds, expiry, engine load, and table fencing before another effect can run.
            </span>
          </span>
        </label>
        <AlertDialogFooter>
          <AlertDialogCancel>Keep unresolved</AlertDialogCancel>
          <AlertDialogAction
            disabled={reason.trim().length < 3 || pending}
            onClick={() => onConfirm(outcome, reason.trim(), continueJob)}
          >
            Record outcome
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
};

const SectionHeading = ({
  icon: Icon,
  title,
  detail,
  count
}: {
  icon: typeof Activity;
  title: string;
  detail: string;
  count?: number;
}) => (
  <div className="mb-2 mt-6 flex flex-wrap items-end justify-between gap-3">
    <div className="flex items-start gap-2.5">
      <Icon className="mt-0.5 size-4 text-muted-foreground" aria-hidden />
      <div>
        <h2 className="text-sm font-semibold text-foreground">{title}</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">{detail}</p>
      </div>
    </div>
    {count !== undefined ? <span className="text-xs tabular-nums text-muted-foreground">{count} shown</span> : null}
  </div>
);

export const ControlRoom = () => {
  const { api, role } = useAuth();
  const queryClient = useQueryClient();
  const canOperate = role === "operator" || role === "admin";
  const canEmergencyStop = role === "admin";
  const [effectSelection, setEffectSelection] = useState<RowSelectionState>({});

  const summary = useQuery({
    queryKey: [...CONTROL_ROOM_KEY, "summary", api.token],
    queryFn: () => getMaintenanceSafetySummary(api),
    refetchInterval: 10_000
  });
  const controls = useQuery({
    queryKey: [...CONTROL_ROOM_KEY, "controls", api.token],
    queryFn: () => listMaintenanceControls(api),
    refetchInterval: 10_000
  });
  const effects = useQuery({
    queryKey: [...CONTROL_ROOM_KEY, "effects", api.token],
    queryFn: () => listMaintenanceEffects(api, { ambiguousOnly: true, limit: 100 }),
    refetchInterval: 10_000
  });
  const intents = useQuery({
    queryKey: [...CONTROL_ROOM_KEY, "intents", api.token],
    queryFn: () => listMaintenanceIntents(api, { limit: 100 }),
    refetchInterval: 10_000
  });
  const destinations = useQuery({
    queryKey: ["destinations", api.token],
    queryFn: () => listDestinations(api)
  });

  const refresh = () => void queryClient.invalidateQueries({ queryKey: CONTROL_ROOM_KEY });
  const pause = useMutation({
    mutationFn: ({
      scopeType,
      scopeId,
      reason
    }: {
      scopeType: "global" | "destination";
      scopeId: string;
      reason: string;
    }) => pauseMaintenance(api, { scope_type: scopeType, scope_id: scopeId, reason }),
    onSuccess: (control) => {
      toast.success(control.scope_type === "global" ? "Global maintenance hold engaged" : "Destination hold engaged");
      refresh();
    },
    onError: (error) => toast.error((error as Error).message)
  });
  const resume = useMutation({
    mutationFn: ({ control, reason }: { control: MaintenanceControlRead; reason: string }) =>
      resumeMaintenance(api, {
        scope_type: control.scope_type,
        scope_id: control.scope_id,
        reason
      }),
    onSuccess: () => {
      toast.success("Maintenance scope resumed");
      refresh();
    },
    onError: (error) => toast.error((error as Error).message)
  });
  const resolveEffect = useMutation({
    mutationFn: ({
      effect,
      outcome,
      reason,
      continueJob
    }: {
      effect: EffectReceiptRead;
      outcome: "succeeded" | "failed";
      reason: string;
      continueJob: boolean;
    }) =>
      resolveMaintenanceEffect(api, effect.id, {
        outcome,
        reason,
        evidence_reference: effect.external_reference ?? undefined,
        continue_job: continueJob
      }),
    onSuccess: (result) => {
      toast.success(result.message);
      refresh();
    },
    onError: (error) => toast.error((error as Error).message)
  });
  const resolveEffectsBatch = useMutation({
    mutationFn: ({
      effects,
      outcome,
      reason,
      continueJob
    }: {
      effects: EffectReceiptRead[];
      outcome: "succeeded" | "failed";
      reason: string;
      continueJob: boolean;
    }) =>
      resolveMaintenanceEffectsBatch(api, {
        receipt_ids: effects.map((effect) => effect.id),
        outcome,
        reason,
        continue_job: continueJob
      }),
    onSuccess: (result) => {
      if (result.failed > 0) {
        toast.warning(`${result.resolved} effects resolved; ${result.failed} still need review`);
      } else {
        toast.success(`${result.resolved} effects resolved`);
      }
      setEffectSelection({});
      refresh();
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const effectColumns = useMemo<ColumnDef<EffectReceiptRead, unknown>[]>(
    () => [
      {
        id: "state",
        header: "Outcome",
        size: 126,
        cell: ({ row }) => <StateBadge state={row.original.state} />
      },
      {
        id: "table",
        header: "Effect target",
        cell: ({ row }) => (
          <div className="min-w-[13rem]">
            <Link to={`/tables/${row.original.table_id}`} className="font-medium text-foreground hover:text-primary">
              {row.original.table_ref}
            </Link>
            <p className="mt-0.5 text-xs text-muted-foreground">
              step {row.original.step_index + 1} · {row.original.operation_type.replaceAll("_", " ")}
            </p>
          </div>
        )
      },
      {
        id: "reference",
        header: "External evidence",
        cell: ({ row }) => (
          <div className="min-w-[11rem]">
            <p className="font-mono text-xs text-foreground">{row.original.external_reference ?? "No stable reference"}</p>
            <p className="mt-0.5 text-xs text-muted-foreground">{row.original.strategy.replaceAll("_", " ")}</p>
          </div>
        )
      },
      {
        id: "probes",
        header: "Reconciliation",
        size: 138,
        cell: ({ row }) => (
          <div>
            <p className="tabular-nums text-foreground">{row.original.probe_count} probes</p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {row.original.next_probe_at ? `Next ${formatTime(row.original.next_probe_at)}` : "Awaiting operator"}
            </p>
          </div>
        )
      },
      {
        id: "job",
        header: "",
        size: 176,
        cell: ({ row }) => (
          <div className="flex items-center justify-end gap-1">
            <Button size="sm" variant="ghost" asChild>
              <Link to={`/jobs/${row.original.job_id}`}>Open job</Link>
            </Button>
            <ResolveEffect
              effect={row.original}
              disabled={!canOperate}
              pending={resolveEffect.isPending}
              onConfirm={(outcome, reason, continueJob) =>
                resolveEffect.mutate({ effect: row.original, outcome, reason, continueJob })
              }
            />
          </div>
        )
      }
    ],
    [canOperate, resolveEffect]
  );

  const intentColumns = useMemo<ColumnDef<MaintenanceIntentRead, unknown>[]>(
    () => [
      {
        id: "status",
        header: "State",
        size: 132,
        cell: ({ row }) => <StateBadge state={row.original.status} />
      },
      {
        id: "scope",
        header: "Bound scope",
        cell: ({ row }) => (
          <div className="min-w-[13rem]">
            <Link to={`/tables/${row.original.table_id}`} className="font-medium text-foreground hover:text-primary">
              {row.original.table_ref}
            </Link>
            <p className="mt-0.5 text-xs text-muted-foreground">{row.original.operation_scope.join(" → ")}</p>
          </div>
        )
      },
      {
        id: "lineage",
        header: "Lineage",
        size: 132,
        cell: ({ row }) => (
          <div className="flex items-center gap-2">
            <Fingerprint className="size-3.5 text-muted-foreground" aria-hidden />
            <span className="text-xs tabular-nums">
              v{row.original.version} / fence {row.original.fencing_epoch}
            </span>
          </div>
        )
      },
      {
        id: "plan",
        header: "Immutable plan",
        size: 146,
        cell: ({ row }) => (
          <div>
            <p className="font-mono text-xs">{shortId(row.original.plan_hash)}</p>
            <p className="mt-0.5 text-xs text-muted-foreground">{row.original.containment_action.replaceAll("_", " ")}</p>
          </div>
        )
      },
      {
        id: "expiry",
        header: "Expiry",
        size: 150,
        cell: ({ row }) => formatTime(row.original.expires_at)
      },
      {
        id: "job",
        header: "",
        size: 96,
        cell: ({ row }) => (
          <Button size="sm" variant="ghost" asChild>
            <Link to={`/jobs/${row.original.job_id}`}>Open job</Link>
          </Button>
        )
      }
    ],
    []
  );

  const destinationNames = useMemo(
    () => new Map((destinations.data ?? []).map((destination) => [destination.id, destination.name])),
    [destinations.data]
  );

  const pauseColumns = useMemo<ColumnDef<MaintenanceControlRead, unknown>[]>(
    () => [
      {
        id: "scope",
        header: "Held scope",
        cell: ({ row }) => (
          <div>
            <p className="font-medium">
              {row.original.scope_type === "destination"
                ? (destinationNames.get(row.original.scope_id) ?? "Destination")
                : row.original.scope_type === "global"
                  ? "All maintenance"
                  : "Tenant"}
            </p>
            <p className="mt-0.5 font-mono text-xs text-muted-foreground">
              {row.original.scope_type === "global" ? "global" : `${row.original.scope_type} · ${row.original.scope_id}`}
            </p>
          </div>
        )
      },
      { id: "reason", header: "Reason", cell: ({ row }) => row.original.reason ?? "No reason recorded" },
      {
        id: "generation",
        header: "Control generation",
        size: 154,
        cell: ({ row }) => <span className="tabular-nums">#{row.original.generation}</span>
      },
      {
        id: "changed",
        header: "Changed",
        size: 190,
        cell: ({ row }) => (
          <div>
            <p>{row.original.changed_by}</p>
            <p className="mt-0.5 text-xs text-muted-foreground">{formatTime(row.original.updated_at)}</p>
          </div>
        )
      },
      {
        id: "actions",
        header: "",
        size: 100,
        cell: ({ row }) => (
          <ResumeControl
            control={row.original}
            disabled={!canOperate}
            pending={resume.isPending}
            onConfirm={(control, reason) => resume.mutate({ control, reason })}
          />
        )
      }
    ],
    [canOperate, destinationNames, resume]
  );

  const activeControls = (controls.data ?? []).filter((control) => control.paused);
  const holdTargets = useMemo<HoldTarget[]>(() => {
    const ids = new Set([
      ...destinationNames.keys(),
      ...[...(effects.data ?? []), ...(intents.data ?? [])].map((item) => item.destination_id)
    ]);
    return Array.from(ids)
      .map((id) => ({ id, label: destinationNames.get(id) ?? id }))
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [destinationNames, effects.data, intents.data]);
  const selectedEffects = (effects.data ?? []).filter((effect) => effectSelection[effect.id]);
  const queryError = summary.error ?? controls.error ?? effects.error ?? intents.error;

  return (
    <div>
      <PageHeader
        title="Control Room"
        subtitle="The durable execution ledger for holds, ambiguous effects, immutable intents, and fencing."
        rootCrumb={{ label: "Control Room", to: "/control-room" }}
        actions={
          <Button variant="secondary" onClick={refresh}>
            <RefreshCw className="size-4" aria-hidden />
            Refresh
          </Button>
        }
      />

      <QueryErrorAlert error={queryError} title="Safety state is incomplete" />
      <AsyncBoundary
        isLoading={summary.isLoading}
        isError={summary.isError}
        error={summary.error}
        onRetry={() => void summary.refetch()}
        skeleton={<Skeleton className="mb-5 h-28 w-full" />}
      >
        {summary.data ? (
          <SafetyRail
            summary={summary.data}
            actions={
              <div className="flex flex-wrap gap-2">
                <TargetedHold
                  targets={holdTargets}
                  disabled={!canOperate}
                  pending={pause.isPending}
                  onConfirm={(destinationId, reason) =>
                    pause.mutate({ scopeType: "destination", scopeId: destinationId, reason })
                  }
                />
                <EmergencyStop
                  disabled={
                    !canEmergencyStop ||
                    (controls.data ?? []).some((control) => control.paused && control.scope_type === "global")
                  }
                  pending={pause.isPending}
                  onConfirm={(reason) =>
                    pause.mutate({ scopeType: "global", scopeId: "global", reason })
                  }
                />
              </div>
            }
          />
        ) : null}
      </AsyncBoundary>

      {!canOperate ? (
        <p className="mb-4 text-xs text-muted-foreground">
          Viewer access is read-only. Operator or admin role is required to change a safety control.
        </p>
      ) : !canEmergencyStop ? (
        <p className="mb-4 text-xs text-muted-foreground">
          Global emergency stop requires admin role. Operators can resume safety scopes they are authorized to manage.
        </p>
      ) : null}

      {activeControls.length > 0 ? (
        <>
          <SectionHeading
            icon={CirclePause}
            title="Active holds"
            detail="Generation-stamped controls checked by admission and again before effect submission."
            count={activeControls.length}
          />
          <DataTable
            columns={pauseColumns}
            data={activeControls}
            loading={controls.isLoading}
            density="compact"
            getRowId={(row) => `${row.scope_type}:${row.scope_id}`}
          />
        </>
      ) : null}

      <SectionHeading
        icon={TriangleAlert}
        title="Effects requiring proof"
        detail="These effects are deliberately unreplayable until an engine probe or operator evidence resolves the outcome."
        count={effects.data?.length ?? 0}
      />
      <DataTable
        columns={effectColumns}
        data={effects.data ?? []}
        loading={effects.isLoading}
        density="compact"
        getRowId={(row) => row.id}
        rowSelection={effectSelection}
        onRowSelectionChange={setEffectSelection}
        enableRowSelection={canOperate}
        toolbar={
          <div className="flex min-h-8 flex-wrap items-center justify-between gap-2">
            <p className="text-xs text-muted-foreground">
              {selectedEffects.length === 0
                ? "Select receipts only when one evidence source proves the same outcome."
                : `${selectedEffects.length} receipt${selectedEffects.length === 1 ? "" : "s"} selected`}
            </p>
            <ResolveEffectsBatch
              count={selectedEffects.length}
              disabled={!canOperate}
              pending={resolveEffectsBatch.isPending}
              onConfirm={(outcome, reason, continueJob) =>
                resolveEffectsBatch.mutate({
                  effects: selectedEffects,
                  outcome,
                  reason,
                  continueJob
                })
              }
            />
          </div>
        }
        emptyTitle="No ambiguous effects"
        emptyDescription="All submitted external effects have a known outcome. Cryo can continue safely."
      />

      <SectionHeading
        icon={RadioTower}
        title="Intent and fence lineage"
        detail="Every run is bound to one canonical plan, approval set, budget, expiry, and monotonic table fence."
        count={intents.data?.length ?? 0}
      />
      <DataTable
        columns={intentColumns}
        data={intents.data ?? []}
        loading={intents.isLoading}
        density="compact"
        getRowId={(row) => row.id}
        emptyTitle="No durable intents"
        emptyDescription="Durable intent binding is feature-gated. New maintenance runs will appear here when enabled."
      />
    </div>
  );
};
