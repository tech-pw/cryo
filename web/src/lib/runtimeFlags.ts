import { apiFetch, type ApiOptions } from "../api/client";

export type RuntimeFlags = {
  native_engine_enabled: boolean;
  maintenance_writes_enabled: boolean;
  native_engine_fallback: string;
  /** IANA zone the install schedules and reports in (CRYO_DISPLAY_TIMEZONE). */
  display_timezone?: string;
};

export const getRuntimeFlags = (api: ApiOptions): Promise<RuntimeFlags> =>
  apiFetch<RuntimeFlags>("/runtime", api);
