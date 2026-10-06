import { createContext, useContext, type ReactNode } from "react";

interface ShellContextValue {
  openCommandPalette: () => void;
  closeMobileNav: () => void;
  openAssistant: (question?: string, context?: object) => void;
}

const ShellContext = createContext<ShellContextValue | null>(null);

export const ShellProvider = ({
  children,
  value
}: {
  children: ReactNode;
  value: ShellContextValue;
}) => <ShellContext.Provider value={value}>{children}</ShellContext.Provider>;

export const useShell = (): ShellContextValue => {
  const ctx = useContext(ShellContext);
  if (!ctx) {
    throw new Error("useShell must be used within ShellProvider");
  }
  return ctx;
};

export const useShellOptional = (): ShellContextValue | null => useContext(ShellContext);
