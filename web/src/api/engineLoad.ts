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
  workers_busy?: number | null;
  /** Admitted jobs no worker has taken yet. */
  admitted_waiting?: number | null;
  oldest_waiting_seconds?: number | null;
}

const waitLabel = (seconds: number): string =>
  seconds < 60 ? `${Math.round(seconds)}s` : seconds < 3600 ? `${Math.round(seconds / 60)} min` : `${(seconds / 3600).toFixed(1)} h`;

/**
 * Names the bottleneck when admitted work is waiting (#490): workers when an engine still
 * has open slots, engine slots when every engine is full. Null when nothing is waiting.
 */
export const drainHint = (load: EngineLoad | undefined): string | null => {
  const waiting = load?.admitted_waiting ?? 0;
  if (!load || waiting <= 0) return null;
  const oldest = load.oldest_waiting_seconds != null ? `, oldest ${waitLabel(load.oldest_waiting_seconds)}` : "";
  const openSlots = load.engines.some((row) => row.admission === "open" && row.slots_used < row.slots_limit);
  const jobs = `${waiting} admitted ${waiting === 1 ? "job" : "jobs"} waiting${oldest}`;
  return openSlots
    ? `Workers are the bottleneck: ${jobs}. Add workers or turn on async Spark dispatch.`
    : `Engine slots are the bottleneck: ${jobs}.`;
};

export const getEngineLoad = (api: ApiOptions) => apiFetch<EngineLoad>("/engine-load", api);
