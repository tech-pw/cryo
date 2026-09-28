import { apiFetch, type ApiOptions } from "../api/client";

export type RuntimeFlags = {
  native_engine_enabled: boolean;
  maintenance_writes_enabled: boolean;
  native_engine_fallback: string;
};

export const getRuntimeFlags = (api: ApiOptions): Promise<RuntimeFlags> =>
  apiFetch<RuntimeFlags>("/runtime", api);
