import { apiFetch, type ApiOptions } from "./client";

export type PathStepKind = "automatic" | "approval" | "design" | "external";

export interface PathStepRead {
  id: string;
  kind: PathStepKind;
  title: string;
  detail: string;
  fixes: string[];
  projected_score: number;
  projected_grade: string;
  confidence: "measured" | "estimated" | "assumed";
  evidence: string[];
  action: { type: string; [key: string]: unknown } | null;
  required_role: "operator" | "admin" | null;
  confirm_text: string | null;
  in_schedule: boolean;
}

export interface PathDeduction {
  label: string;
  deduction: number;
  severity: string;
  reason: string;
}

export interface PathToARead {
  table_id: string;
  score: number;
  grade: string;
  deductions: PathDeduction[];
  steps: PathStepRead[];
  cryo_score: number;
  cryo_grade: string;
  reachable_score: number;
  reachable_grade: string;
  notes: string[];
  design_findings?: PathDesignFinding[];
}

export interface PathDesignFinding {
  label: string;
  severity: string;
  reason: string;
}

export interface PathToAApplyResponse {
  step_id: string;
  status: string;
  message: string;
  run_id: string | null;
  audit_log_id: number | null;
}

export const getPathToA = (api: ApiOptions, tableId: string) =>
  apiFetch<PathToARead>(`/tables/${tableId}/path-to-a`, api);

export const applyPathStep = (api: ApiOptions, tableId: string, body: { step_id: string; confirm?: string }) =>
  apiFetch<PathToAApplyResponse>(`/tables/${tableId}/path-to-a/apply`, api, { method: "POST", body });
