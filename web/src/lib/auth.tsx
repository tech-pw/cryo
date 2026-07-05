import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { ApiOptions } from "../api/client";
import type { RoleToken } from "../api/types";

const ROLE_STORAGE_KEY = "cryo.devRole";
const SUBJECT_STORAGE_KEY = "cryo.devSubject";
const TOKEN_STORAGE_KEY = "cryo.accessToken";
const AUTH_MODE_STORAGE_KEY = "cryo.authMode";
const TOKENS: RoleToken[] = ["viewer", "operator", "admin"];

export type AuthMode = "dev" | "oidc";

interface OidcPublicConfig {
  client_id: string;
  authorization_endpoint: string;
  token_endpoint: string;
  redirect_uri: string;
}

interface AuthConfigResponse {
  mode: AuthMode;
  dev_token_enabled: boolean;
  oidc?: OidcPublicConfig | null;
}

interface AuthContextValue {
  role: RoleToken;
  subject: string;
  token: string;
  api: ApiOptions;
  loading: boolean;
  error: string | null;
  isAuthenticated: boolean;
  authMode: AuthMode;
  devTokenEnabled: boolean;
  signIn: (role: RoleToken, subject: string) => Promise<void>;
  startOidcSignIn: () => void;
  completeOidcSignIn: (code: string) => Promise<void>;
  signOut: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

async function fetchAuthConfig(): Promise<AuthConfigResponse> {
  const response = await fetch("/api/v1/auth/config");
  if (!response.ok) {
    throw new Error(`Auth config unavailable (${response.status})`);
  }
  return (await response.json()) as AuthConfigResponse;
}

async function fetchAccessToken(role: RoleToken, subject: string): Promise<string> {
  const response = await fetch("/api/v1/auth/token", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role, subject })
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Token request failed (${response.status})`);
  }
  const payload = (await response.json()) as { access_token: string };
  return payload.access_token;
}

async function exchangeOidcCode(code: string, redirectUri: string): Promise<string> {
  const response = await fetch("/api/v1/auth/oidc/exchange", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code, redirect_uri: redirectUri })
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `OIDC exchange failed (${response.status})`);
  }
  const payload = (await response.json()) as { access_token: string };
  return payload.access_token;
}

function decodeJwtSubject(token: string): string {
  const parts = token.split(".");
  if (parts.length < 2) return "sso-user";
  try {
    const payload = JSON.parse(atob(parts[1].replace(/-/g, "+").replace(/_/g, "/"))) as {
      sub?: string;
      preferred_username?: string;
      email?: string;
      roles?: string[];
      role?: string;
    };
    return payload.preferred_username || payload.email || payload.sub || "sso-user";
  } catch {
    return "sso-user";
  }
}

function decodeJwtRole(token: string): RoleToken {
  const parts = token.split(".");
  if (parts.length < 2) return "viewer";
  try {
    const payload = JSON.parse(atob(parts[1].replace(/-/g, "+").replace(/_/g, "/"))) as {
      roles?: string[] | string;
      role?: string;
      scope?: string;
    };
    const raw: string[] = [];
    if (typeof payload.role === "string") raw.push(payload.role);
    if (typeof payload.scope === "string") raw.push(...payload.scope.split(" "));
    if (Array.isArray(payload.roles)) raw.push(...payload.roles.map(String));
    if (typeof payload.roles === "string") raw.push(...payload.roles.split(" "));
    const normalized = raw.map((value) => value.toLowerCase());
    if (normalized.includes("admin")) return "admin";
    if (normalized.includes("operator")) return "operator";
    return "viewer";
  } catch {
    return "viewer";
  }
}

export const AuthProvider = ({ children }: { children: ReactNode }) => {
  const [role, setRole] = useState<RoleToken>("operator");
  const [subject, setSubject] = useState("");
  const [accessToken, setAccessToken] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [authMode, setAuthMode] = useState<AuthMode>("dev");
  const [devTokenEnabled, setDevTokenEnabled] = useState(true);
  const [oidcConfig, setOidcConfig] = useState<OidcPublicConfig | null>(null);

  const persistSession = (
    nextRole: RoleToken,
    nextSubject: string,
    token: string,
    mode: AuthMode
  ) => {
    setRole(nextRole);
    setSubject(nextSubject);
    setAccessToken(token);
    setAuthMode(mode);
    window.localStorage.setItem(ROLE_STORAGE_KEY, nextRole);
    window.localStorage.setItem(SUBJECT_STORAGE_KEY, nextSubject);
    window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
    window.localStorage.setItem(AUTH_MODE_STORAGE_KEY, mode);
  };

  const signIn = async (nextRole: RoleToken, nextSubject: string) => {
    setLoading(true);
    setError(null);
    try {
      const trimmed = nextSubject.trim() || "dev-ui";
      const token = await fetchAccessToken(nextRole, trimmed);
      persistSession(nextRole, trimmed, token, "dev");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to obtain access token");
      setAccessToken("");
      throw err;
    } finally {
      setLoading(false);
    }
  };

  const startOidcSignIn = () => {
    if (!oidcConfig) {
      setError("OIDC is not configured");
      return;
    }
    const state = crypto.randomUUID();
    window.sessionStorage.setItem("cryo.oidc.state", state);
    const url = new URL(oidcConfig.authorization_endpoint);
    url.searchParams.set("client_id", oidcConfig.client_id);
    url.searchParams.set("redirect_uri", oidcConfig.redirect_uri);
    url.searchParams.set("response_type", "code");
    url.searchParams.set("scope", "openid profile email");
    url.searchParams.set("state", state);
    window.location.assign(url.toString());
  };

  const completeOidcSignIn = async (code: string) => {
    if (!oidcConfig) {
      throw new Error("OIDC is not configured");
    }
    setLoading(true);
    setError(null);
    try {
      const token = await exchangeOidcCode(code, oidcConfig.redirect_uri);
      const nextSubject = decodeJwtSubject(token);
      const nextRole = decodeJwtRole(token);
      persistSession(nextRole, nextSubject, token, "oidc");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "OIDC sign-in failed");
      setAccessToken("");
      throw err;
    } finally {
      setLoading(false);
    }
  };

  const signOut = () => {
    setAccessToken("");
    setSubject("");
    setRole("operator");
    setError(null);
    window.localStorage.removeItem(TOKEN_STORAGE_KEY);
    window.localStorage.removeItem(SUBJECT_STORAGE_KEY);
    window.localStorage.removeItem(ROLE_STORAGE_KEY);
    window.localStorage.removeItem(AUTH_MODE_STORAGE_KEY);
  };

  useEffect(() => {
    let cancelled = false;
    fetchAuthConfig()
      .then((config) => {
        if (cancelled) return;
        setAuthMode(config.mode);
        setDevTokenEnabled(config.dev_token_enabled);
        setOidcConfig(config.oidc ?? null);

        const storedToken = window.localStorage.getItem(TOKEN_STORAGE_KEY);
        const storedSubject = window.localStorage.getItem(SUBJECT_STORAGE_KEY);
        const storedMode = window.localStorage.getItem(AUTH_MODE_STORAGE_KEY) as AuthMode | null;

        if (storedToken && storedSubject) {
          if (storedMode === "oidc") {
            persistSession(decodeJwtRole(storedToken), storedSubject, storedToken, "oidc");
            return;
          }
          if (config.dev_token_enabled) {
            const storedRole = window.localStorage.getItem(ROLE_STORAGE_KEY);
            const nextRole = TOKENS.includes(storedRole as RoleToken)
              ? (storedRole as RoleToken)
              : "operator";
            return fetchAccessToken(nextRole, storedSubject).then((token) => {
              if (cancelled) return;
              persistSession(nextRole, storedSubject, token, "dev");
            });
          }
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load auth config");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      role,
      subject,
      token: accessToken,
      api: { token: accessToken },
      loading,
      error,
      isAuthenticated: Boolean(accessToken && subject),
      authMode,
      devTokenEnabled,
      signIn,
      startOidcSignIn,
      completeOidcSignIn,
      signOut
    }),
    [role, subject, accessToken, loading, error, authMode, devTokenEnabled]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export const useAuth = (): AuthContextValue => {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
};

export const roleOptions = TOKENS.map((value) => ({ label: value, value }));
