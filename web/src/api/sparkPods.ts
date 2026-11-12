import { apiFetch, type ApiOptions } from "./client";

export interface SparkPod {
  role: string;
  name: string;
  phase: string | null;
  waiting_reason: string | null;
  waiting_message: string | null;
  waiting_seconds: number | null;
  node: string | null;
  stuck: boolean;
}

export interface SparkPodEvent {
  pod: string;
  reason: string | null;
  message: string | null;
  count: number;
  last_seen: string | null;
}

export interface SparkPods {
  available: boolean;
  reason: string | null;
  engine_ref: string | null;
  pods: SparkPod[];
  events: SparkPodEvent[];
  summary: string | null;
}

export const getSparkPods = (api: ApiOptions, jobId: string) => apiFetch<SparkPods>(`/jobs/${jobId}/spark-pods`, api);

export const podStateLabel = (pod: SparkPod): string => {
  const state = pod.waiting_reason ?? pod.phase ?? "Unknown";
  if (pod.waiting_seconds == null || (!pod.waiting_reason && pod.phase !== "Pending")) return state;
  const minutes = Math.floor(pod.waiting_seconds / 60);
  return `${state} · ${minutes < 1 ? "<1" : minutes}m`;
};
