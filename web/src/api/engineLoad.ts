import { apiFetch, type ApiOptions } from "./client";

export interface EngineLoadRow {
  destination_id: string;
  name: string;
  engine: string;
  slots_used: number;
  slots_limit: number;
  admission: string;
  live_probe: string;
}

export const getEngineLoad = (api: ApiOptions) =>
  apiFetch<{ engines: EngineLoadRow[] }>("/engine-load", api);
