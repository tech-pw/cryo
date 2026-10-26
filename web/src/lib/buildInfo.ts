/**
 * Which build is running (#263). The web bundle's own version comes from the
 * VITE_CRYO_VERSION / VITE_CRYO_GIT_SHA build args (web/Dockerfile.prod); the API's from
 * GET /api/v1/version. A dev server or a build without the args reports "dev".
 */
import { useEffect, useState } from "react";

import { API_PREFIX } from "../api/client";

export interface ApiVersion {
  version: string;
  git_sha: string | null;
  built_at: string | null;
  schema_revision: string | null;
}

export interface WebBuild {
  version: string;
  gitSha: string | null;
}

export const DEV_VERSION = "dev";

export const webBuild = (env: Record<string, string | undefined> = import.meta.env): WebBuild => ({
  version: env.VITE_CRYO_VERSION?.trim() || DEV_VERSION,
  gitSha: env.VITE_CRYO_GIT_SHA?.trim() || null
});

export const shortSha = (sha: string | null | undefined): string | null => (sha ? sha.slice(0, 7) : null);

/**
 * True only when both sides report a real build and they differ: a dev web build
 * against any API (or the reverse) is normal during development.
 */
export const versionsDiffer = (web: WebBuild, api: ApiVersion | undefined): boolean => {
  if (!api || web.version === DEV_VERSION) return false;
  if (web.gitSha && api.git_sha) return web.gitSha !== api.git_sha;
  return web.version !== api.version;
};

export const getApiVersion = async (): Promise<ApiVersion> => {
  const response = await fetch(`${API_PREFIX}/version`);
  if (!response.ok) throw new Error(`GET ${API_PREFIX}/version failed: ${response.status}`);
  return (await response.json()) as ApiVersion;
};

// One request per page load, shared by the sidebar footer and Settings > About. Plain
// state rather than React Query so the shell renders without a QueryClientProvider.
let cached: Promise<ApiVersion> | null = null;

export const resetApiVersionCache = () => {
  cached = null;
};

export const useApiVersion = (): { data: ApiVersion | undefined; error: unknown } => {
  const [state, setState] = useState<{ data: ApiVersion | undefined; error: unknown }>({
    data: undefined,
    error: null
  });
  useEffect(() => {
    let active = true;
    if (typeof fetch !== "function") return;
    cached ??= getApiVersion().catch((error: unknown) => {
      cached = null;
      throw error;
    });
    cached.then(
      (data) => active && setState({ data, error: null }),
      (error: unknown) => active && setState({ data: undefined, error })
    );
    return () => {
      active = false;
    };
  }, []);
  return state;
};

/** One-line build summary for bug reports. */
export const buildSummary = (web: WebBuild, api: ApiVersion | undefined): string => {
  const apiPart = api
    ? `api ${api.version}${api.git_sha ? ` (${shortSha(api.git_sha)})` : ""}, schema ${api.schema_revision ?? "unknown"}`
    : "api unknown";
  return `Cryo ${apiPart}; web ${web.version}${web.gitSha ? ` (${shortSha(web.gitSha)})` : ""}`;
};
