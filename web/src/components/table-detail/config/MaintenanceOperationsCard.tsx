import { useMutation, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { AlertTriangle, Ban, CircleCheck, ExternalLink, ShieldAlert, Undo2 } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { putOperationExclusions } from "../../../api/client";
import type { MaintenanceOperationRead, TableConfigResponse, TableOperationsRead } from "../../../api/types";
import { useAuth } from "../../../lib/auth";
import { cn } from "../../../lib/cn";
import type { WriteAccess } from "../../../lib/permissions";
import { Button } from "../../ui/button";
import { Checkbox } from "../../ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../ui/dialog";
import { Switch } from "../../ui/switch";
import { Textarea } from "../../ui/textarea";
import { MIN_REASON_LENGTH } from "./ConfigReviewDialog";
import { InfoButton, RiskPill } from "./RiskPopover";
import {
  companionWarnings,
  describeApplyError,
  diffExclusions,
  ENGINE_LABEL,
  OPERATION_STATUS_LABEL,
  OPERATION_STATUS_TONE
} from "./tableConfig";

export const OPERATIONS_SECTION_ID = "tc-operations";

const PHASES: { id: MaintenanceOperationRead["phase"]; label: string; description: string }[] = [
  {
    id: "compaction",
    label: "Compaction",
    description: "Rewrite data and delete files so reads stay fast. Runs first."
  },
  {
    id: "cleanup",
    label: "Cleanup",
    description: "Free storage and keep metadata small, after compaction in the same run."
  }
];

export interface MaintenanceOperationsCardProps {
  tableId: string;
  operations: TableOperationsRead;
  configKey: QueryKey;
  access: WriteAccess;
}

export const MaintenanceOperationsCard = ({ tableId, operations, configKey, access }: MaintenanceOperationsCardProps) => {
  const { api } = useAuth();
  const queryClient = useQueryClient();
  const ops = operations.operations;
  const saved = useMemo(() => new Set(ops.filter((op) => op.excluded).map((op) => op.operation)), [ops]);
  const [draft, setDraft] = useState<Set<string> | null>(null);
  const [reviewOpen, setReviewOpen] = useState(false);
  const current = draft ?? saved;
  const diff = diffExclusions(ops, current);
  const changed = diff.exclude.length + diff.include.length;
  const warnings = companionWarnings(ops, current);

  const toggle = (operation: string, runs: boolean) => {
    const next = new Set(current);
    if (runs) next.delete(operation);
    else next.add(operation);
    const same = next.size === saved.size && [...next].every((op) => saved.has(op));
    setDraft(same ? null : next);
  };

  const save = useMutation({
    mutationFn: ({ reason, confirm }: { reason: string; confirm: boolean }) =>
      putOperationExclusions(api, tableId, { excluded: [...current], reason, confirm }),
    onSuccess: (result) => {
      queryClient.setQueryData<TableConfigResponse>(configKey, (old) =>
        old ? { ...old, operations: result.operations } : old
      );
      setDraft(null);
      setReviewOpen(false);
      toast.success(result.message);
      void queryClient.invalidateQueries({ queryKey: ["table-audit", tableId] });
    },
    onError: (error: Error) => toast.error(describeApplyError(error))
  });

  const excludedCount = saved.size;

  return (
    <section id={OPERATIONS_SECTION_ID} tabIndex={-1} className="tc-group tc-ops" aria-labelledby="tc-ops-title">
      <header className="tc-group-head">
        <div>
          <h3 id="tc-ops-title">Maintenance operations</h3>
          <p>
            Choose which operations Cryo may run on this table. Exclusions apply to every run that plans it:
            scheduled presets, ad-hoc runs and the nightly batch.
          </p>
        </div>
        <span className="tc-group-meta">
          {operations.preset_name
            ? `${operations.preset_name} · ${operations.mode ?? "?"} mode`
            : "No preset"}
          {excludedCount ? ` · ${excludedCount} excluded` : ""}
        </span>
      </header>

      {operations.held ? (
        <div className="tc-banner tc-ops-banner" role="status">
          <AlertTriangle aria-hidden="true" />
          <span>
            <strong>This table is on hold.</strong> A hold in Runs → Holds stops all maintenance on it, whatever is
            chosen here.
          </span>
        </div>
      ) : null}

      <div className="tc-ops-cols" aria-hidden="true">
        <span>Operation</span>
        <span>For this table</span>
        <span>If excluded</span>
        <span>Run it</span>
      </div>

      {PHASES.map((phase) => {
        const rows = ops.filter((op) => op.phase === phase.id);
        return (
          <div key={phase.id} role="group" aria-label={phase.label}>
            <div className="tc-ops-phase">
              <span>{phase.label}</span>
              <span>{phase.description}</span>
            </div>
            {rows.map((op) => (
              <OperationRow
                key={op.operation}
                op={op}
                runs={!current.has(op.operation)}
                staged={current.has(op.operation) !== op.excluded}
                access={access}
                onToggle={(runs) => toggle(op.operation, runs)}
              />
            ))}
          </div>
        );
      })}

      {warnings.length ? (
        <div className="tc-ops-foot">
          {warnings.map((warning) => (
            <span key={warning.operation} className="tc-warning">
              <AlertTriangle aria-hidden="true" />
              {warning.hint}
            </span>
          ))}
        </div>
      ) : null}

      {changed ? (
        <div className="tc-ops-bar" role="region" aria-label="Staged operation changes">
          <span className="tc-bar-text">
            <span>{changed === 1 ? "1 operation change" : `${changed} operation changes`}</span>
            <span>Nothing changes until you review and save.</span>
          </span>
          <span className="tc-bar-buttons">
            <Button variant="ghost" onClick={() => setDraft(null)}>
              Discard
            </Button>
            <Button
              variant="primary"
              disabled={!access.allowed}
              title={access.reason ?? undefined}
              onClick={() => setReviewOpen(true)}
            >
              Review and save
            </Button>
          </span>
        </div>
      ) : null}

      <Dialog open={reviewOpen} onOpenChange={setReviewOpen}>
        <DialogContent className="max-w-xl">
          {reviewOpen ? (
            <OperationsReview
              ops={ops}
              exclude={diff.exclude}
              include={diff.include}
              warnings={warnings.map((w) => w.hint)}
              saving={save.isPending}
              onCancel={() => setReviewOpen(false)}
              onSave={(args) => save.mutate(args)}
            />
          ) : null}
        </DialogContent>
      </Dialog>
    </section>
  );
};

const OperationRow = ({
  op,
  runs,
  staged,
  access,
  onToggle
}: {
  op: MaintenanceOperationRead;
  runs: boolean;
  staged: boolean;
  access: WriteAccess;
  onToggle: (runs: boolean) => void;
}) => {
  const tone = OPERATION_STATUS_TONE[op.status];
  return (
    <div
      className={cn("tc-row", staged && "tc-row--staged", !staged && op.excluded && "tc-row--excluded")}
      data-testid={`operation-${op.operation}`}
    >
      <div className="tc-ops-row">
        <div className="tc-name">
          <span className="tc-name-label">
            {op.label}
            <InfoButton label={op.label}>
              <OperationInfo op={op} />
            </InfoButton>
          </span>
          <span className="tc-key">{op.operation}</span>
          <span className="tc-summary-text tc-clamp">{op.summary}</span>
        </div>

        <div className="tc-cell">
          <span className="tc-cell-label">For this table</span>
          {staged ? (
            <span className={cn("tc-status", runs ? "tc-status--ok" : "tc-status--danger")}>
              {runs ? "Will be re-enabled" : "Will be excluded"}
            </span>
          ) : (
            <span className={cn("tc-status", `tc-status--${tone}`)}>
              {tone === "ok" ? <CircleCheck aria-hidden="true" /> : tone === "danger" ? <Ban aria-hidden="true" /> : null}
              {OPERATION_STATUS_LABEL[op.status]}
            </span>
          )}
          <span className="tc-note">{staged ? "Applies from the next plan built for this table." : op.status_detail}</span>
          {op.excluded && !staged ? (
            <span className="tc-editor-hint">
              By {op.excluded_by ?? "unknown"}
              {op.excluded_at ? ` · ${new Date(op.excluded_at).toLocaleDateString()}` : ""}
              {op.excluded_reason ? ` · “${op.excluded_reason}”` : ""}
            </span>
          ) : null}
        </div>

        <div className="tc-cell">
          <span className="tc-cell-label">If excluded</span>
          <RiskPill risk={op.exclude_risk} />
          <span className="tc-summary-text tc-clamp">{op.exclude_consequence}</span>
        </div>

        <div className="tc-ops-toggle">
          <Switch
            checked={runs}
            disabled={!access.allowed}
            title={access.reason ?? undefined}
            onCheckedChange={onToggle}
            aria-label={`Run ${op.label}`}
          />
          <span className="tc-editor-hint">{runs ? "On" : "Off"}</span>
          {staged ? (
            <button
              type="button"
              className="tc-ops-undo"
              onClick={() => onToggle(!runs)}
              aria-label={`Undo change to ${op.label}`}
            >
              <Undo2 aria-hidden="true" />
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
};

const OperationInfo = ({ op }: { op: MaintenanceOperationRead }) => (
  <div className="tc-pop">
    <div>
      <h4>{op.label}</h4>
      <p className="tc-summary-text tc-pop-summary">{op.summary}</p>
    </div>
    <dl className="tc-pop-facts">
      <dt>Planned when</dt>
      <dd>{op.when_planned}</dd>
      <dt>If excluded</dt>
      <dd>
        <RiskPill risk={op.exclude_risk} className="tc-inline" /> {op.exclude_consequence}
      </dd>
    </dl>
    {(op.reasons_to_exclude ?? []).length ? (
      <div>
        <div className="tc-pop-section-title">Good reasons to exclude it</div>
        <ul className="tc-pop-list tc-pop-bullets">
          {(op.reasons_to_exclude ?? []).map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      </div>
    ) : null}
    {(op.engine_notes ?? []).length ? (
      <div>
        <div className="tc-pop-section-title">Engines</div>
        <ul className="tc-pop-list">
          {(op.engine_notes ?? []).map((note) => (
            <li key={note.engine} className="tc-engine">
              <span className="tc-engine-name">{ENGINE_LABEL[note.engine] ?? note.engine}</span>
              <span>{note.note}</span>
            </li>
          ))}
        </ul>
      </div>
    ) : null}
    <a className="tc-doc" href={op.doc_url} target="_blank" rel="noreferrer">
      Documentation <ExternalLink aria-hidden="true" />
    </a>
  </div>
);

const OperationsReview = ({
  ops,
  exclude,
  include,
  warnings,
  saving,
  onCancel,
  onSave
}: {
  ops: MaintenanceOperationRead[];
  exclude: string[];
  include: string[];
  warnings: string[];
  saving: boolean;
  onCancel: () => void;
  onSave: (args: { reason: string; confirm: boolean }) => void;
}) => {
  const [reason, setReason] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const byKey = new Map(ops.map((op) => [op.operation, op]));
  const highRisk = exclude.filter((key) => byKey.get(key)?.exclude_risk === "high");
  const reasonOk = reason.trim().length >= MIN_REASON_LENGTH;
  const canSave = reasonOk && (!highRisk.length || acknowledged);

  return (
    <>
      <DialogHeader>
        <DialogTitle>Review operation changes</DialogTitle>
        <DialogDescription>
          Saved for this table in Cryo and audited. Nothing runs now; the next plan built for this table follows it.
        </DialogDescription>
      </DialogHeader>

      <div className="tc-review">
        {exclude.map((key) => {
          const op = byKey.get(key);
          if (!op) return null;
          return (
            <div key={key} className={cn("tc-review-item", op.exclude_risk === "high" && "tc-review-item--high")}>
              <div className="tc-review-head">
                <span>Exclude {op.label}</span>
                <RiskPill risk={op.exclude_risk} />
              </div>
              <span className="tc-key">{op.operation}</span>
              <span className="tc-summary-text">{op.exclude_consequence}</span>
            </div>
          );
        })}
        {include.map((key) => {
          const op = byKey.get(key);
          if (!op) return null;
          return (
            <div key={key} className="tc-review-item">
              <div className="tc-review-head">
                <span>Re-enable {op.label}</span>
                <span className="tc-status tc-status--ok">
                  <CircleCheck aria-hidden="true" />
                  Cryo default
                </span>
              </div>
              <span className="tc-key">{op.operation}</span>
              <span className="tc-summary-text">{op.when_planned}</span>
            </div>
          );
        })}
        {warnings.map((warning) => (
          <span key={warning} className="tc-warning">
            <AlertTriangle aria-hidden="true" />
            {warning}
          </span>
        ))}
      </div>

      {highRisk.length ? (
        <div className="tc-confirm">
          <span className="tc-warning tc-warning--blocked">
            <ShieldAlert aria-hidden="true" />
            Excluding {highRisk.map((key) => byKey.get(key)?.label).join(", ")} lets storage and metadata grow without
            limit.
          </span>
          <label>
            <Checkbox
              checked={acknowledged}
              onCheckedChange={(checked) => setAcknowledged(checked === true)}
              aria-label="I understand the consequences"
            />
            <span>I have read the consequences above and want to exclude it.</span>
          </label>
        </div>
      ) : null}

      <Textarea
        aria-label="Reason for this change"
        placeholder="Why? Recorded in the audit log and shown on the table."
        value={reason}
        maxLength={500}
        rows={2}
        onChange={(event) => setReason(event.target.value)}
      />
      {reason && !reasonOk ? <span className="tc-editor-hint">At least {MIN_REASON_LENGTH} characters.</span> : null}

      <DialogFooter>
        <Button variant="ghost" onClick={onCancel}>
          Keep editing
        </Button>
        <Button
          variant={highRisk.length ? "danger" : "primary"}
          disabled={!canSave}
          loading={saving}
          onClick={() => onSave({ reason: reason.trim(), confirm: highRisk.length > 0 && acknowledged })}
        >
          Save
        </Button>
      </DialogFooter>
    </>
  );
};
