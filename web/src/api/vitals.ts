import { apiFetch, type ApiOptions } from "./client";

export interface CryoVitalsDeduction {
  reason: string;
  points: number;
}

export interface CryoVitalsImpact {
  direction?: string | null;
  measurement_state?: string | null;
  gb_freed_yesterday?: number | null;
  dod_pct?: number | null;
  wow_pct?: number | null;
  actions_with_bytes_evidence?: number | null;
  actions_succeeded?: number | null;
  measured?: number | null;
  succeeded?: number | null;
}

export interface CryoVitalsNorthStar {
  byte_actions_measured?: number | null;
  byte_actions_succeeded?: number | null;
}

export interface CryoVitals {
  generated_at?: string;
  score?: number | null;
  status?: string | null;
  headline?: string | null;
  deductions?: CryoVitalsDeduction[];
  impact?: CryoVitalsImpact | null;
  north_star?: CryoVitalsNorthStar | null;
}

export const getCryoVitals = (api: ApiOptions) => apiFetch<CryoVitals>("/vitals", api);
