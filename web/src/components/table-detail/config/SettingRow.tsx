import { AlertTriangle, ArrowRight, CircleCheck, Lock, OctagonX, Pencil, Sparkles, Undo2 } from "lucide-react";

import type { ConfigChangePreview, TableSettingRead } from "../../../api/types";
import { cn } from "../../../lib/cn";
import type { WriteAccess } from "../../../lib/permissions";
import { Button } from "../../ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "../../ui/tooltip";
import { InfoButton, RiskPopover, SettingConsequences } from "./RiskPopover";
import { SettingEditor } from "./SettingEditor";
import { formatValue, settingAnchor, SOURCE_HINT, SOURCE_LABEL, type StagedChange } from "./tableConfig";

export interface SettingRowProps {
  setting: TableSettingRead;
  columns: string[];
  staged?: StagedChange;
  preview?: ConfigChangePreview;
  editing: boolean;
  /** Why this row cannot be edited right now, or null when it can. */
  lockedReason: string | null;
  access: WriteAccess;
  onEdit: () => void;
  onCancel: () => void;
  onStage: (value: string | null) => void;
  onUnstage: () => void;
}

export const SettingRow = ({
  setting,
  columns,
  staged,
  preview,
  editing,
  lockedReason,
  access,
  onEdit,
  onCancel,
  onStage,
  onUnstage
}: SettingRowProps) => {
  const differs = setting.matches_recommendation === false;
  const canEdit = lockedReason == null && access.allowed;
  const risk = staged && preview && !preview.blocked ? preview.risk : setting.change_risk;

  return (
    <div
      className={cn("tc-row", differs && !staged && "tc-row--differs", staged && "tc-row--staged")}
      id={settingAnchor(setting.key)}
      tabIndex={-1}
      data-testid={`setting-${setting.key}`}
    >
      <div className="tc-row-main">
        <div className="tc-name">
          <span className="tc-name-label">
            {setting.label}
            <InfoButton label={setting.label}>
              <SettingConsequences setting={setting} />
            </InfoButton>
          </span>
          <span className="tc-key">{setting.key}</span>
          <span className="tc-summary-text tc-clamp">{setting.summary}</span>
        </div>

        <div className="tc-cell">
          <span className="tc-cell-label">Current</span>
          {staged ? (
            <span className="tc-diff" aria-label="Staged change">
              <span className="tc-diff-old">{formatValue(setting, setting.current_value)}</span>
              <ArrowRight aria-hidden="true" className="size-3 text-muted-foreground" />
              <span className="tc-diff-new">
                {staged.value === null ? `${formatValue(setting, null)} (unset)` : formatValue(setting, staged.value)}
              </span>
            </span>
          ) : (
            <span className={cn("tc-value", !setting.is_set && "tc-value--muted")}>
              {formatValue(setting, setting.current_value)}
            </span>
          )}
          <Tooltip>
            <TooltipTrigger asChild>
              <span className={cn("tc-source", `tc-source--${setting.current_source}`)} tabIndex={0}>
                {SOURCE_LABEL[setting.current_source]}
              </span>
            </TooltipTrigger>
            <TooltipContent className="max-w-64">{SOURCE_HINT[setting.current_source]}</TooltipContent>
          </Tooltip>
        </div>

        <div className="tc-cell">
          <span className="tc-cell-label">Cryo recommends</span>
          <Recommendation setting={setting} canApply={canEdit && !staged} onApply={onStage} />
        </div>

        <div className="tc-cell">
          <span className="tc-cell-label">Change risk</span>
          <RiskPopover setting={setting} risk={risk} />
          <span className="tc-editor-hint">{staged ? "this change" : "if changed"}</span>
        </div>

        <div className="tc-actions">
          {staged ? (
            <Button size="sm" variant="ghost" onClick={onUnstage} aria-label={`Undo staged change to ${setting.label}`}>
              <Undo2 aria-hidden="true" />
              Undo
            </Button>
          ) : null}
          {lockedReason ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="tc-locked" tabIndex={0} aria-label={`Read-only: ${lockedReason}`}>
                  <Lock aria-hidden="true" className="size-3" />
                  Read-only
                </span>
              </TooltipTrigger>
              <TooltipContent className="max-w-72">{lockedReason}</TooltipContent>
            </Tooltip>
          ) : !editing ? (
            <Button
              size="sm"
              variant="outline"
              disabled={!access.allowed}
              title={access.reason ?? undefined}
              onClick={onEdit}
              aria-label={`Edit ${setting.label}`}
            >
              <Pencil aria-hidden="true" />
              Edit
            </Button>
          ) : null}
        </div>
      </div>

      {staged && preview && (preview.blocked || preview.warnings?.length) ? (
        <div className="tc-staged-meta">
          {preview.blocked ? (
            <span className="tc-warning tc-warning--blocked">
              <OctagonX aria-hidden="true" />
              {preview.blocked}
            </span>
          ) : null}
          {(preview.warnings ?? []).map((warning) => (
            <span key={warning} className="tc-warning">
              <AlertTriangle aria-hidden="true" />
              {warning}
            </span>
          ))}
        </div>
      ) : null}

      {editing ? (
        <SettingEditor
          setting={setting}
          columns={columns}
          initialValue={staged ? staged.value : setting.current_value ?? null}
          onStage={onStage}
          onCancel={onCancel}
        />
      ) : null}
    </div>
  );
};

const Recommendation = ({
  setting,
  canApply,
  onApply
}: {
  setting: TableSettingRead;
  canApply: boolean;
  onApply: (value: string) => void;
}) => {
  if (setting.recommended_value != null && setting.matches_recommendation) {
    return (
      <>
        <span className="tc-reco tc-reco--match">
          <CircleCheck aria-hidden="true" />
          Matches recommendation
        </span>
        {setting.note ? <span className="tc-note">{setting.note}</span> : null}
      </>
    );
  }
  if (setting.recommended_value != null) {
    const recommended = setting.recommended_value;
    return (
      <>
        <span className="tc-reco tc-reco--differs">
          <Sparkles aria-hidden="true" />
          <span className="tc-value">{formatValue(setting, recommended)}</span>
        </span>
        {setting.recommendation_reason ? <span className="tc-reco-reason">{setting.recommendation_reason}</span> : null}
        {setting.note ? <span className="tc-note">{setting.note}</span> : null}
        {canApply ? (
          <button type="button" className="tc-apply-reco" onClick={() => onApply(recommended)}>
            Stage recommendation
            {setting.recommendation_risk ? ` · ${setting.recommendation_risk} risk` : ""}
          </button>
        ) : null}
      </>
    );
  }
  if (setting.note) return <span className="tc-note">{setting.note}</span>;
  return <span className="tc-value tc-value--muted">No change suggested</span>;
};
