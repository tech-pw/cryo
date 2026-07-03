import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import type { ApiOptions } from "../api/client";
import type { RoleToken } from "../api/types";

const STORAGE_KEY = "cryo.devRoleToken";
const TOKENS: RoleToken[] = ["viewer", "operator", "admin"];

interface AuthContextValue {
  token: RoleToken;
  setToken: (token: RoleToken) => void;
  api: ApiOptions;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export const AuthProvider = ({ children }: { children: ReactNode }) => {
  const [token, setTokenState] = useState<RoleToken>(() => {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return TOKENS.includes(stored as RoleToken) ? (stored as RoleToken) : "operator";
  });

  const setToken = (next: RoleToken) => {
    setTokenState(next);
    window.localStorage.setItem(STORAGE_KEY, next);
  };

  const value = useMemo<AuthContextValue>(() => ({ token, setToken, api: { token } }), [token]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export const useAuth = (): AuthContextValue => {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
};

export const roleOptions = TOKENS.map((value) => ({ label: value, value }));
