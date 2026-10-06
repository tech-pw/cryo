import { type CSSProperties } from "react";
import { plotValue } from "./plotValue";

export { plotValue };

export const MISSING_HATCH_ID = "cryo-not-measured";
export const MISSING_MARK_MIN_PX = 8;

const hatchStyle: CSSProperties = {
  backgroundImage:
    "repeating-linear-gradient(-45deg, transparent, transparent 2px, currentColor 2px, currentColor 3px)",
  minHeight: MISSING_MARK_MIN_PX,
  height: MISSING_MARK_MIN_PX,
  width: MISSING_MARK_MIN_PX
};

export interface MissingPatternProps {
  label?: string;
}

export function MissingPattern({ label = "Not measured" }: MissingPatternProps) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
      <span
        aria-hidden="true"
        data-testid="missing-hatch"
        className="inline-block rounded-sm border border-current"
        style={hatchStyle}
      />
      <span>{label}</span>
    </span>
  );
}

export interface MissingPatternDefsProps {
  id?: string;
}

export function MissingPatternDefs({ id = MISSING_HATCH_ID }: MissingPatternDefsProps) {
  return (
    <defs>
      <pattern id={id} width="8" height="8" patternUnits="userSpaceOnUse">
        <path d="M0 8 L8 0" stroke="currentColor" strokeWidth="1" />
      </pattern>
    </defs>
  );
}
