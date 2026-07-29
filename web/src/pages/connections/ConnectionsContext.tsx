import { createContext, useContext, type ReactNode } from "react";
import { useConnectionsPage, type ConnectionsPageState } from "./useConnectionsPage";

const ConnectionsContext = createContext<ConnectionsPageState | null>(null);

export const ConnectionsProvider = ({ children }: { children: ReactNode }) => {
  const value = useConnectionsPage();
  return <ConnectionsContext.Provider value={value}>{children}</ConnectionsContext.Provider>;
};

export const useConnectionsContext = (): ConnectionsPageState => {
  const ctx = useContext(ConnectionsContext);
  if (!ctx) {
    throw new Error("useConnectionsContext must be used within ConnectionsProvider");
  }
  return ctx;
};
