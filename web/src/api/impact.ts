import { apiFetch, type ApiOptions } from "./client";
import type { ImpactDay } from "../lib/impactHistory";

export interface ImpactHistoryResponse {
  since: string;
  until: string;
  rows: ImpactDay[];
}

export const getImpactHistory = (api: ApiOptions, since: string, until: string) =>
  apiFetch<ImpactHistoryResponse>("/impact/history", api, { query: { since, until } });
