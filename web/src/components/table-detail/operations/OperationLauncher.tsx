import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowUpRight, Play, ShoppingCart, X } from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { getEffectiveParams, getEffectivePreset, getTable, runTableOperations } from "../../../api/client";
import { useAuth } from "../../../lib/auth";
import { useWriteControl } from "../../../lib/useWriteControl";
import { tableKeys } from "../../../pages/table-detail/tableKeys";
import { Button } from "../../ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../ui/dialog";
import { OPERATION_CATALOG, RUNNABLE_OPERATIONS, type RunnableOperation } from "./operationCatalog";
import "./operations.css";

interface LauncherValue {
  /** Operations queued for the next run, in execution order. */
  cart: RunnableOperation[];
  inCart: (op: RunnableOperation) => boolean;
  toggle: (op: RunnableOperation) => void;
  setCart: (ops: RunnableOperation[]) => void;
  clear: () => void;
  /** Add one operation and open the review straight away (the "Fix" buttons). */
  launch: (op: RunnableOperation) => void;
  review: () => void;
  pending: boolean;
  /** Why nothing can run right now, or null when it can. */
  blockedReason: string | null;
  lastRunId: string | null;
}

const LauncherContext = createContext<LauncherValue | null>(null);

export const useOperationLauncher = (): LauncherValue | null => useContext(LauncherContext);

const ordered = (ops: Iterable<RunnableOperation>): RunnableOperation[] => {
  const set = new Set(ops);
  return RUNNABLE_OPERATIONS.filter((op) => set.has(op));
};

const numberParam = (params: Record<string, unknown> | undefined, key: string): number | null => {
  const value = params?.[key];
  const parsed = typeof value === "string" ? Number(value) : value;
  return typeof parsed === "number" && Number.isFinite(parsed) ? parsed : null;
};

export const settingLines = (
  op: RunnableOperation,
  params: Record<string, unknown> | undefined,
  commitsPerDay: number | null
): string[] => {
  if (op === "expire_snapshots") {
    const days = numberParam(params, "retention_days") ?? 7;
    const lines = [`Keeps snapshots from the last ${days} day${days === 1 ? "" : "s"}; protected snapshots are never expired.`];
    if (commitsPerDay != null && commitsPerDay >= 20) {
      lines.push(`~${Math.round(commitsPerDay)} commits/day, so the planner may tighten retention to 1–3 days (recorded in the run's plan notes).`);
    }
    return lines;
  }
  if (op === "remove_orphan_files") {
    const days = numberParam(params, "orphan_retention_days") ?? numberParam(params, "retention_days") ?? 7;
    return [`Only unreferenced files older than ${days} day${days === 1 ? "" : "s"}, so in-flight writes are safe.`];
  }
  if (op === "optimize") {
    const min = numberParam(params, "min_small_files");
    return [`Only partitions the scanner flagged, in budgeted chunks${min != null ? `; skips partitions with fewer than ${min} small files` : ""}.`];
  }
  return ["Metadata only; data files are not touched."];
};

export const OperationLauncherProvider = ({ tableId, tableName, children }: { tableId: string; tableName: string; children: ReactNode }) => {
  const { api, token } = useAuth();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const access = useWriteControl("runMaintenance");
  const [cart, setCartState] = useState<RunnableOperation[]>([]);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [lastRunId, setLastRunId] = useState<string | null>(null);

  const preset = useQuery({
    queryKey: tableKeys.effectivePreset(tableId, token),
    queryFn: () => getEffectivePreset(api, tableId),
    enabled: Boolean(tableId)
  });
  const params = useQuery({
    queryKey: tableKeys.effectiveParams(tableId, token),
    queryFn: () => getEffectiveParams(api, tableId),
    enabled: reviewOpen,
    retry: false
  });
  const table = useQuery({
    queryKey: tableKeys.detail(tableId, token),
    queryFn: () => getTable(api, tableId),
    enabled: reviewOpen
  });

  const hasWorkflow = Boolean(preset.data?.preset_id);
  const blockedReason = !access.allowed
    ? access.reason ?? "Your role can't run maintenance"
    : preset.isSuccess && !hasWorkflow
      ? "Assign a workflow first. It supplies the engine and retention settings."
      : null;

  const run = useMutation({
    mutationFn: (ops: RunnableOperation[]) => runTableOperations(api, tableId, { operations: ops }),
    onSuccess: (result, ops) => {
      setLastRunId(result.run_id);
      setReviewOpen(false);
      setCartState([]);
      toast.success(ops.length === 1 ? `${OPERATION_CATALOG[ops[0]].title} queued` : `${ops.length} operations queued as one run`, {
        description: "Follow it in Maintenance → Runs. It starts when engine capacity is available."
      });
      const next = new URLSearchParams(searchParams);
      next.set("tab", "maintenance");
      next.set("run", result.run_id);
      setSearchParams(next, { replace: false });
      window.setTimeout(() => document.getElementById("maintenance-runs")?.scrollIntoView({ behavior: "smooth", block: "start" }), 250);
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      void queryClient.invalidateQueries({ queryKey: ["table-runs", tableId] });
      void queryClient.invalidateQueries({ queryKey: ["table-audit", tableId] });
      void queryClient.invalidateQueries({ queryKey: ["table-maintenance-status", tableId] });
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const setCart = useCallback((ops: RunnableOperation[]) => setCartState(ordered(ops)), []);
  const toggle = useCallback(
    (op: RunnableOperation) => setCartState((current) => (current.includes(op) ? current.filter((item) => item !== op) : ordered([...current, op]))),
    []
  );
  const clear = useCallback(() => setCartState([]), []);
  const review = useCallback(() => {
    setAcknowledged(false);
    setReviewOpen(true);
  }, []);
  const launch = useCallback(
    (op: RunnableOperation) => {
      setCartState((current) => ordered([...current, op]));
      review();
    },
    [review]
  );

  const value = useMemo<LauncherValue>(
    () => ({
      cart,
      inCart: (op) => cart.includes(op),
      toggle,
      setCart,
      clear,
      launch,
      review,
      pending: run.isPending,
      blockedReason,
      lastRunId
    }),
    [cart, toggle, setCart, clear, launch, review, run.isPending, blockedReason, lastRunId]
  );

  const commitsPerDay = numberParam((table.data?.stats?.raw ?? undefined) as Record<string, unknown> | undefined, "commits_per_day");
  const destructive = cart.some((op) => OPERATION_CATALOG[op].destructive);
  const canSubmit = cart.length > 0 && !blockedReason && (!destructive || acknowledged) && !run.isPending;

  return (
    <LauncherContext.Provider value={value}>
      {children}
      {cart.length > 0 && !reviewOpen ? (
        <div className="td-cart-bar" role="region" aria-label="Run cart">
          <span className="td-cart-bar-icon" aria-hidden="true">
            <ShoppingCart size={16} />
            <b>{cart.length}</b>
          </span>
          <span className="td-cart-bar-text">
            <b>{cart.length === 1 ? "1 operation" : `${cart.length} operations`} ready</b>
            <span>{cart.map((op) => OPERATION_CATALOG[op].title).join(" → ")}</span>
          </span>
          <Button type="button" size="sm" variant="ghost" onClick={clear}>
            Clear
          </Button>
          <Button type="button" size="sm" variant="primary" onClick={review}>
            Review & run
          </Button>
        </div>
      ) : null}
      <Dialog open={reviewOpen} onOpenChange={(open) => (open ? null : setReviewOpen(false))}>
        <DialogContent className="td-op-dialog">
          <DialogHeader>
            <span className="td-op-dialog-kicker" data-destructive={destructive || undefined}>
              {destructive ? "Deletes files" : "Safe to run"}
            </span>
            <DialogTitle>Run {cart.length === 1 ? OPERATION_CATALOG[cart[0]].title.toLowerCase() : `${cart.length} operations`}</DialogTitle>
            <DialogDescription>
              On <code>{tableName}</code>, as one run, in this order. Only these operations run, not the rest of the workflow.
            </DialogDescription>
          </DialogHeader>

          {cart.length === 0 ? (
            <p className="td-cart-note">The cart is empty. Add an operation from the Run on demand section.</p>
          ) : (
            <ol className="td-cart-steps">
              {cart.map((op, index) => {
                const info = OPERATION_CATALOG[op];
                return (
                  <li key={op}>
                    <span className="td-cart-step-no">{index + 1}</span>
                    <div>
                      <p>
                        <b>{info.title}</b>
                        {info.destructive ? <span className="td-cart-step-flag">deletes files</span> : null}
                      </p>
                      {(params.isPending ? ["Loading workflow settings…"] : settingLines(op, params.data?.effective_params, commitsPerDay)).map((line) => (
                        <p key={line} className="td-cart-step-note">
                          {line}
                        </p>
                      ))}
                    </div>
                    <Button type="button" size="icon" variant="ghost" className="size-7" aria-label={`Remove ${info.title}`} onClick={() => toggle(op)}>
                      <X className="!size-3.5" aria-hidden="true" />
                    </Button>
                  </li>
                );
              })}
            </ol>
          )}
          <p className="td-cart-note">
            Engine and settings come from the <b>{preset.data?.preset_name ?? "assigned"}</b> workflow.
          </p>

          {destructive ? (
            <label className="td-op-dialog-ack">
              <input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} />
              <span>
                <AlertTriangle size={14} aria-hidden="true" /> I understand files are deleted permanently.
              </span>
            </label>
          ) : null}
          {blockedReason ? <p className="td-op-dialog-blocked">{blockedReason}</p> : null}

          <DialogFooter>
            <Button type="button" variant="secondary" onClick={() => setReviewOpen(false)}>
              Keep editing
            </Button>
            <Button
              type="button"
              variant={destructive ? "danger" : "primary"}
              disabled={!canSubmit}
              loading={run.isPending}
              onClick={() => run.mutate(cart)}
            >
              <Play aria-hidden="true" />
              {cart.length === 1 ? `${OPERATION_CATALOG[cart[0]].verb} now` : `Run ${cart.length} operations`}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </LauncherContext.Provider>
  );
};

export const LastRunLink = () => {
  const launcher = useOperationLauncher();
  if (!launcher?.lastRunId) return null;
  return (
    <Link className="td-overview-link" to={`/runs/${launcher.lastRunId}`}>
      Follow the run you just started <ArrowUpRight size={14} aria-hidden="true" />
    </Link>
  );
};
