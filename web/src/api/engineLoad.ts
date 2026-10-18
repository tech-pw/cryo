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

export interface EngineLoad {
  engines: EngineLoadRow[];
  /** Null when Redis is unreachable; never a fabricated 0. */
  workers_online?: number | null;
}

export const getEngineLoad = (api: ApiOptions) => apiFetch<EngineLoad>("/engine-load", api);
