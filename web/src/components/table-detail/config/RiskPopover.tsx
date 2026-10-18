import { ExternalLink, Info } from "lucide-react";
import type { ReactNode } from "react";

import type { ConfigRisk, TableSettingRead } from "../../../api/types";
import { cn } from "../../../lib/cn";
import { Popover, PopoverContent, PopoverTrigger } from "../../ui/popover";
import { ENGINE_LABEL, RISK_LABEL } from "./tableConfig";

export const RiskPill = ({ risk, className }: { risk: ConfigRisk; className?: string }) => (
  <span className={cn("tc-risk tc-risk--static", `tc-risk--${risk}`, className)}>
    <span className="tc-risk-dot" aria-hidden="true" />
    {RISK_LABEL[risk]}
  </span>
);

/**
 * The risk shown on a row is the worst outcome of any direction the setting can move
 * in; the popover breaks that down per direction so "High" is never unexplained.
 */
export const RiskPopover = ({ setting, risk }: { setting: TableSettingRead; risk?: ConfigRisk }) => {
  const shown = risk ?? setting.change_risk;
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className={cn("tc-risk", `tc-risk--${shown}`)}
          aria-label={`${RISK_LABEL[shown]} risk. What changing ${setting.label} does`}
        >
          <span className="tc-risk-dot" aria-hidden="true" />
          {RISK_LABEL[shown]}
          <Info aria-hidden="true" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[23rem] max-w-[calc(100vw-2rem)]">
        <SettingConsequences setting={setting} />
      </PopoverContent>
    </Popover>
  );
};

export const InfoButton = ({ label, children }: { label: string; children: ReactNode }) => (
  <Popover>
    <PopoverTrigger asChild>
      <button type="button" className="tc-info" aria-label={`About ${label}`}>
        <Info aria-hidden="true" />
      </button>
    </PopoverTrigger>
    <PopoverContent align="start" className="w-[23rem] max-w-[calc(100vw-2rem)]">
      {children}
    </PopoverContent>
  </Popover>
);

export const SettingConsequences = ({ setting }: { setting: TableSettingRead }) => (
  <div className="tc-pop">
    <div>
      <h4>{setting.label}</h4>
      <p className="tc-summary-text tc-pop-summary">{setting.summary}</p>
    </div>

    <dl className="tc-pop-facts">
      <dt>Takes effect</dt>
      <dd>{setting.takes_effect}</dd>
      <dt>Reversible</dt>
      <dd>{setting.reversible}</dd>
    </dl>

    {(setting.risk_directions ?? []).length ? (
      <div>
        <div className="tc-pop-section-title">Risk of each change</div>
        <ul className="tc-pop-list">
          {(setting.risk_directions ?? []).map((direction) => (
            <li key={direction.label} className="tc-pop-direction">
              <span className="tc-pop-direction-label">{direction.label}</span>
              {direction.blocked ? (
                <span className="tc-risk tc-risk--static tc-risk--high">Blocked</span>
              ) : (
                <RiskPill risk={direction.risk} />
              )}
              {direction.blocked ? (
                <span className="tc-pop-direction-blocked">{direction.blocked}</span>
              ) : direction.detail ? (
                <span className="tc-pop-direction-detail">{direction.detail}</span>
              ) : null}
            </li>
          ))}
        </ul>
      </div>
    ) : null}

    {(setting.engine_notes ?? []).length ? (
      <div>
        <div className="tc-pop-section-title">Engines</div>
        <ul className="tc-pop-list">
          {(setting.engine_notes ?? []).map((note) => (
            <li key={note.engine} className="tc-engine">
              <span className="tc-engine-name">{ENGINE_LABEL[note.engine] ?? note.engine}</span>
              <span>{note.note}</span>
            </li>
          ))}
        </ul>
      </div>
    ) : null}

    <a className="tc-doc" href={setting.doc_url} target="_blank" rel="noreferrer">
      Documentation <ExternalLink aria-hidden="true" />
    </a>
  </div>
);
