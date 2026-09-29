import { useLocation, useParams } from "react-router-dom";

export const usePageContext = (): Record<string, string> => {
  const { pathname, search } = useLocation();
  const params = useParams();
  const context: Record<string, string> = { route: pathname };
  if (params.tableId) context.table_id = params.tableId;
  if (params.runId) context.run_id = params.runId;
  const tab = new URLSearchParams(search).get("tab");
  if (tab) context.tab = tab;
  return context;
};
