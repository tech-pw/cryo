import { createContext, useContext } from "react";
import type { TableDetailState } from "./useTableDetail";

const TableDetailContext = createContext<TableDetailState | null>(null);

export const TableDetailProvider = TableDetailContext.Provider;

export const useTableDetailContext = (): TableDetailState => {
  const ctx = useContext(TableDetailContext);
  if (!ctx) {
    throw new Error("useTableDetailContext must be used within TableDetailProvider");
  }
  return ctx;
};
