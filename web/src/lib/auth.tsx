import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { ApiOptions } from "../api/client";
import type { RoleToken } from "../api/types";

const ROLE_STORAGE_KEY = "cryo.devRole";
const SUBJECT_STORAGE_KEY = "cryo.devSubject";
const TOKEN_STORAGE_KEY = "cryo.accessToken";
const REFRESH_STORAGE_KEY = "cryo.refreshToken";
const AUTH_MODE_STORAGE_KEY = "cryo.authMode";
export const OIDC_STATE_STORAGE_KEY = "cryo.oidc.state";
const OIDC_VERIFIER_STORAGE_KEY = "cryo.oidc.verifier";
const TOKENS: RoleToken[] = ["viewer", "operator", "admin"];

export type AuthMode = "dev" | "oidc" | "local";

/**
 * Distinguishes why an OIDC authorization-code exchange failed so LoginCallback can render
 * a specific, non-technical recovery outcome instead of one generic error string.
 * "rejected" covers any non-2xx response from the token endpoint (expired/invalid code,
 * misconfiguration, etc.); "network" covers the request never reaching the server at all.
 */
export type OidcExchangeErrorKind = "rejected" | "network";

export class OidcExchangeError extends Error {
  readonly kind: OidcExchangeErrorKind;
  readonly status?: number;

  constructor(kind: OidcExchangeErrorKind, message: string, status?: number) {
    super(message);
    this.name = "OidcExchangeError";
    this.kind = kind;
    this.status = status;
  }
}

interface OidcPublicConfig {
  client_id: string;
  authorization_endpoint: string;
  token_endpoint: string;
  redirect_uri: string;
}

interface GooglePublicConfig {
  client_id: string;
  hosted_domain?: string | null;
}

interface AuthConfigResponse {
  mode: AuthMode;
  dev_token_enabled: boolean;
  dev_token_enabled_in_prod: boolean;
  password_login_enabled?: boolean;
  oidc?: OidcPublicConfig | null;
  google?: GooglePublicConfig | null;
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
  devTokenEnabledInProd: boolean;
  passwordLoginEnabled: boolean;
  googleClientId: string | null;
  signIn: (role: RoleToken, subject: string) => Promise<void>;
  signInWithPassword: (username: string, password: string) => Promise<void>;
  signInWithGoogle: (idToken: string) => Promise<void>;
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

interface TokenPayload {
  access_token: string;
  refresh_token?: string | null;
  expires_in?: number | null;
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
  const payload = (await response.json()) as TokenPayload;
  return payload.access_token;
}

async function exchangeOidcCode(
  code: string,
  redirectUri: string,
  codeVerifier: string | null
): Promise<TokenPayload> {
  const body: { code: string; redirect_uri: string; code_verifier?: string } = {
    code,
    redirect_uri: redirectUri
  };
  if (codeVerifier) {
    body.code_verifier = codeVerifier;
  }
  let response: Response;
  try {
    response = await fetch("/api/v1/auth/oidc/exchange", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
  } catch {
    // fetch() rejects only on network/CORS failures, never on HTTP error status codes —
    // this branch means the request never reached the identity provider.
    throw new OidcExchangeError("network", "Could not reach the identity provider.");
  }
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new OidcExchangeError(
      "rejected",
      detail || `Sign-in was rejected (${response.status})`,
      response.status
    );
  }
  return (await response.json()) as TokenPayload;
}

async function refreshOidcAccessToken(refreshToken: string): Promise<TokenPayload> {
  const response = await fetch("/api/v1/auth/oidc/refresh", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken })
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `OIDC refresh failed (${response.status})`);
  }
  return (await response.json()) as TokenPayload;
}

function decodeJwtExpiry(token: string): number | null {
  const parts = token.split(".");
  if (parts.length < 2) return null;
  try {
    const payload = JSON.parse(atob(parts[1].replace(/-/g, "+").replace(/_/g, "/"))) as { exp?: number };
    return typeof payload.exp === "number" ? payload.exp * 1000 : null;
  } catch {
    return null;
  }
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

function base64UrlEncode(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function createPkceVerifier(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return base64UrlEncode(bytes);
}

async function pkceS256Challenge(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64UrlEncode(new Uint8Array(digest));
}

export function oidcCallbackStateMatches(returnedState: string | null): boolean {
  const stored = window.sessionStorage.getItem(OIDC_STATE_STORAGE_KEY);
  window.sessionStorage.removeItem(OIDC_STATE_STORAGE_KEY);
  const matches =
    stored !== null && stored.length > 0 && returnedState !== null && stored === returnedState;
  if (!matches) {
    window.sessionStorage.removeItem(OIDC_VERIFIER_STORAGE_KEY);
  }
  return matches;
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
  const [devTokenEnabledInProd, setDevTokenEnabledInProd] = useState(false);
  const [oidcConfig, setOidcConfig] = useState<OidcPublicConfig | null>(null);
  const [passwordLoginEnabled, setPasswordLoginEnabled] = useState(false);
  const [googleClientId, setGoogleClientId] = useState<string | null>(null);

  const persistSession = (
    nextRole: RoleToken,
    nextSubject: string,
    token: string,
    mode: AuthMode,
    refreshToken?: string | null
  ) => {
    setRole(nextRole);
    setSubject(nextSubject);
    setAccessToken(token);
    setAuthMode(mode);
    window.localStorage.setItem(ROLE_STORAGE_KEY, nextRole);
    window.localStorage.setItem(SUBJECT_STORAGE_KEY, nextSubject);
    window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
    window.localStorage.setItem(AUTH_MODE_STORAGE_KEY, mode);
    if (refreshToken) {
      window.localStorage.setItem(REFRESH_STORAGE_KEY, refreshToken);
    }
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

  const signInWithPassword = async (username: string, password: string) => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch("/api/v1/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password })
      });
      if (!response.ok) throw new Error("Invalid username or password");
      const payload = (await response.json()) as TokenPayload;
      persistSession(decodeJwtRole(payload.access_token), username.trim().toLowerCase(), payload.access_token, "local");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Sign-in failed");
      throw err;
    } finally {
      setLoading(false);
    }
  };

  const signInWithGoogle = async (idToken: string) => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch("/api/v1/auth/google", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id_token: idToken })
      });
      if (!response.ok) throw new Error("Google sign-in was rejected");
      const payload = (await response.json()) as TokenPayload;
      const nextSubject = decodeJwtSubject(payload.access_token);
      persistSession(decodeJwtRole(payload.access_token), nextSubject, payload.access_token, "local");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Google sign-in failed");
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
    const config = oidcConfig;
    const state = crypto.randomUUID();
    let verifier: string;
    try {
      verifier = createPkceVerifier();
    } catch {
      setError("OIDC sign-in could not start");
      return;
    }
    void pkceS256Challenge(verifier).then(
      (challenge) => {
        window.sessionStorage.setItem(OIDC_STATE_STORAGE_KEY, state);
        window.sessionStorage.setItem(OIDC_VERIFIER_STORAGE_KEY, verifier);
        const url = new URL(config.authorization_endpoint);
        url.searchParams.set("client_id", config.client_id);
        url.searchParams.set("redirect_uri", config.redirect_uri);
        url.searchParams.set("response_type", "code");
        url.searchParams.set("scope", "openid profile email offline_access");
        url.searchParams.set("state", state);
        url.searchParams.set("code_challenge", challenge);
        url.searchParams.set("code_challenge_method", "S256");
        window.location.assign(url.toString());
      },
      () => {
        setError("OIDC sign-in could not start");
      }
    );
  };

  const completeOidcSignIn = async (code: string) => {
    if (!oidcConfig) {
      throw new Error("OIDC is not configured");
    }
    setLoading(true);
    setError(null);
    const codeVerifier = window.sessionStorage.getItem(OIDC_VERIFIER_STORAGE_KEY);
    try {
      const payload = await exchangeOidcCode(code, oidcConfig.redirect_uri, codeVerifier);
      window.sessionStorage.removeItem(OIDC_VERIFIER_STORAGE_KEY);
      const nextSubject = decodeJwtSubject(payload.access_token);
      const nextRole = decodeJwtRole(payload.access_token);
      persistSession(nextRole, nextSubject, payload.access_token, "oidc", payload.refresh_token);
    } catch (err: unknown) {
      if (!(err instanceof OidcExchangeError && err.kind === "network")) {
        window.sessionStorage.removeItem(OIDC_VERIFIER_STORAGE_KEY);
      }
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
    window.localStorage.removeItem(REFRESH_STORAGE_KEY);
    window.localStorage.removeItem(SUBJECT_STORAGE_KEY);
    window.localStorage.removeItem(ROLE_STORAGE_KEY);
    window.localStorage.removeItem(AUTH_MODE_STORAGE_KEY);
    window.sessionStorage.removeItem(OIDC_STATE_STORAGE_KEY);
    window.sessionStorage.removeItem(OIDC_VERIFIER_STORAGE_KEY);
  };

  useEffect(() => {
    let cancelled = false;
    fetchAuthConfig()
      .then((config) => {
        if (cancelled) return;
        setAuthMode(config.mode);
        setDevTokenEnabled(config.dev_token_enabled);
        setDevTokenEnabledInProd(config.dev_token_enabled_in_prod === true);
        setOidcConfig(config.oidc ?? null);
        setPasswordLoginEnabled(config.password_login_enabled === true);
        setGoogleClientId(config.google?.client_id ?? null);

        const storedToken = window.localStorage.getItem(TOKEN_STORAGE_KEY);
        const storedSubject = window.localStorage.getItem(SUBJECT_STORAGE_KEY);
        const storedMode = window.localStorage.getItem(AUTH_MODE_STORAGE_KEY) as AuthMode | null;

        if (storedToken && storedSubject) {
          if (storedMode === "local" || storedMode === "oidc") {
            const storedRefresh = window.localStorage.getItem(REFRESH_STORAGE_KEY);
            persistSession(
              decodeJwtRole(storedToken),
              storedSubject,
              storedToken,
              storedMode,
              storedRefresh
            );
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

  useEffect(() => {
    if (authMode !== "oidc" || !accessToken) return undefined;
    const refreshToken = window.localStorage.getItem(REFRESH_STORAGE_KEY);
    if (!refreshToken) return undefined;

    const refreshIfNeeded = async () => {
      const expiresAt = decodeJwtExpiry(accessToken);
      if (expiresAt === null) return;
      const msUntilExpiry = expiresAt - Date.now();
      if (msUntilExpiry > 5 * 60 * 1000) return;
      try {
        const payload = await refreshOidcAccessToken(refreshToken);
        persistSession(
          decodeJwtRole(payload.access_token),
          decodeJwtSubject(payload.access_token),
          payload.access_token,
          "oidc",
          payload.refresh_token ?? refreshToken
        );
      } catch {
        signOut();
      }
    };

    void refreshIfNeeded();
    const timer = window.setInterval(() => {
      void refreshIfNeeded();
    }, 60_000);
    return () => window.clearInterval(timer);
  }, [authMode, accessToken]);

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
      devTokenEnabledInProd,
      passwordLoginEnabled,
      googleClientId,
      signIn,
      signInWithPassword,
      signInWithGoogle,
      startOidcSignIn,
      completeOidcSignIn,
      signOut
    }),
    [
      role,
      subject,
      accessToken,
      loading,
      error,
      authMode,
      devTokenEnabled,
      devTokenEnabledInProd,
      passwordLoginEnabled,
      googleClientId
    ]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export const useAuth = (): AuthContextValue => {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
};

export const roleOptions = TOKENS.map((value) => ({ label: value, value }));
