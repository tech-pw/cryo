import { useEffect, useState } from "react";
import { useLocation, useParams } from "react-router-dom";

export const usePageContext = (): Record<string, string> => {
  const { pathname, search } = useLocation();
  const params = useParams();
  const [heading, setHeading] = useState("");
  const [title, setTitle] = useState("");
  useEffect(() => {
    setTitle(document.title);
    const node = document.querySelector("h1");
    setHeading(node?.textContent?.trim() ?? "");
  }, [pathname, search]);
  const context: Record<string, string> = { route: pathname };
  if (params.tableId) context.table_id = params.tableId;
  if (params.runId) context.run_id = params.runId;
  if (params.jobId) context.job_id = params.jobId;
  const tab = new URLSearchParams(search).get("tab");
  if (tab) context.tab = tab;
  if (title) context.page_title = title;
  if (heading) context.page_heading = heading;
  return context;
};
