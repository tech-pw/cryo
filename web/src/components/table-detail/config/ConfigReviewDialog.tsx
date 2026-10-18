import { AlertTriangle, ArrowRight, OctagonX, ShieldAlert } from "lucide-react";
import { useState } from "react";

import type { ConfigPreviewResponse, TableSettingRead } from "../../../api/types";
import { cn } from "../../../lib/cn";
import { Button } from "../../ui/button";
import { Checkbox } from "../../ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from "../../ui/dialog";
import { Textarea } from "../../ui/textarea";
import { RiskPill } from "./RiskPopover";
import { formatValue } from "./tableConfig";

export const MIN_REASON_LENGTH = 8;

export interface ConfigReviewDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  preview: ConfigPreviewResponse | undefined;
  previewError: string | null;
  loading: boolean;
  settings: Map<string, TableSettingRead>;
  adminAllowed: boolean;
  applying: boolean;
  onApply: (args: { confirm: boolean; reason: string | null }) => void;
}

export const ConfigReviewDialog = (props: ConfigReviewDialogProps) => (
  <Dialog open={props.open} onOpenChange={props.onOpenChange}>
    <DialogContent className="max-w-2xl">
      {props.open ? <ReviewBody {...props} /> : null}
    </DialogContent>
  </Dialog>
);

const ReviewBody = ({
  onOpenChange,
  preview,
  previewError,
  loading,
  settings,
  adminAllowed,
  applying,
  onApply
}: ConfigReviewDialogProps) => {
  const [acknowledged, setAcknowledged] = useState(false);
  const [reason, setReason] = useState("");

  const changes = preview?.changes ?? [];
  const pending = changes.filter((change) => !change.no_op);
  const highRisk = preview?.requires_confirmation ?? false;
  const needsAdmin = pending.some((change) => change.min_role === "admin") && !adminAllowed;
  const reasonOk = reason.trim().length >= MIN_REASON_LENGTH;
  const canApply =
    Boolean(preview) &&
    !loading &&
    !preview?.blocked &&
    pending.length > 0 &&
    !needsAdmin &&
    (!highRisk || (acknowledged && reasonOk));

  const catalogWrites = pending.filter((change) => change.kind !== "cryo_param").length;
  const overrideWrites = pending.length - catalogWrites;

  return (
    <>
      <DialogHeader>
        <DialogTitle>Review {changes.length === 1 ? "1 change" : `${changes.length} changes`}</DialogTitle>
        <DialogDescription>
          {catalogWrites ? `${catalogWrites} written to the Iceberg catalog in one commit` : null}
          {catalogWrites && overrideWrites ? "; " : null}
          {overrideWrites ? `${overrideWrites} saved as Cryo overrides for this table` : null}
          {pending.length ? ". Every change is audited, then the table is re-read from the catalog." : null}
        </DialogDescription>
      </DialogHeader>

      {previewError ? (
        <div className="tc-banner tc-banner--danger" role="alert">
          <OctagonX aria-hidden="true" />
          <span>{previewError}</span>
        </div>
      ) : null}

      {loading && !preview ? <p className="tc-editor-hint">Checking these changes against the live table…</p> : null}

      <div className="tc-review">
        {changes.map((change) => {
          const setting = settings.get(change.key);
          const fmt = (value: string | null | undefined) => (setting ? formatValue(setting, value) : (value ?? "unset"));
          return (
            <div
              key={change.key}
              className={cn(
                "tc-review-item",
                change.risk === "high" && "tc-review-item--high",
                change.blocked && "tc-review-item--blocked"
              )}
            >
              <div className="tc-review-head">
                <span>{change.label}</span>
                {change.blocked ? (
                  <span className="tc-risk tc-risk--static tc-risk--high">Blocked</span>
                ) : change.no_op ? (
                  <span className="tc-editor-hint">No change</span>
                ) : (
                  <RiskPill risk={change.risk} />
                )}
              </div>
              <span className="tc-key">{change.key}</span>
              <span className="tc-diff">
                <span className="tc-diff-old">{fmt(change.old_value)}</span>
                <ArrowRight aria-hidden="true" className="size-3 text-muted-foreground" />
                <span className="tc-diff-new">{change.new_value === null ? `${fmt(null)} (unset)` : fmt(change.new_value)}</span>
              </span>
              {change.blocked ? (
                <span className="tc-warning tc-warning--blocked">
                  <OctagonX aria-hidden="true" />
                  {change.blocked}
                </span>
              ) : null}
              {(change.warnings ?? []).map((warning) => (
                <span key={warning} className="tc-warning">
                  <AlertTriangle aria-hidden="true" />
                  {warning}
                </span>
              ))}
              {change.min_role === "admin" && !adminAllowed ? (
                <span className="tc-warning tc-warning--blocked">
                  <OctagonX aria-hidden="true" />
                  Admin role is required for this setting.
                </span>
              ) : null}
            </div>
          );
        })}
      </div>

      {highRisk && !preview?.blocked ? (
        <div className="tc-confirm">
          <span className="tc-warning tc-warning--blocked">
            <ShieldAlert aria-hidden="true" />
            This batch contains a high-risk change. It can delete data or cannot be undone.
          </span>
          <label>
            <Checkbox
              checked={acknowledged}
              onCheckedChange={(checked) => setAcknowledged(checked === true)}
              aria-label="I understand the consequences"
            />
            <span>I have read the consequences above and want to apply these changes.</span>
          </label>
          <Textarea
            aria-label="Reason for this change"
            placeholder="Why is this change needed? Recorded in the audit log."
            value={reason}
            maxLength={500}
            rows={2}
            onChange={(event) => setReason(event.target.value)}
          />
          {reason && !reasonOk ? (
            <span className="tc-editor-hint">At least {MIN_REASON_LENGTH} characters.</span>
          ) : null}
        </div>
      ) : (
        <Textarea
          aria-label="Reason for this change (optional)"
          placeholder="Reason (optional, recorded in the audit log)"
          value={reason}
          maxLength={500}
          rows={2}
          onChange={(event) => setReason(event.target.value)}
        />
      )}

      <DialogFooter>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>
          Keep editing
        </Button>
        <Button
          variant={highRisk ? "danger" : "primary"}
          disabled={!canApply}
          loading={applying}
          onClick={() => onApply({ confirm: highRisk && acknowledged, reason: reason.trim() || null })}
        >
          {preview?.blocked
            ? "Resolve blocked changes"
            : `Apply ${pending.length === 1 ? "change" : `${pending.length} changes`}`}
        </Button>
      </DialogFooter>
    </>
  );
};
