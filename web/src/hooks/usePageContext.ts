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
  const tableId = params.tableId ?? pathname.match(/^\/tables\/([^/]+)/)?.[1];
  const runId = params.runId ?? pathname.match(/^\/runs\/([^/]+)/)?.[1];
  const jobId = params.jobId ?? pathname.match(/^\/(?:jobs|runs\/jobs)\/([^/]+)/)?.[1];
  const reservedRuns = new Set(["scanner", "batch", "workflows", "jobs"]);
  if (tableId) context.table_id = decodeURIComponent(tableId);
  if (runId && !reservedRuns.has(runId)) context.run_id = decodeURIComponent(runId);
  if (jobId) context.job_id = decodeURIComponent(jobId);
  const tab = new URLSearchParams(search).get("tab");
  if (tab) context.tab = tab;
  if (title) context.page_title = title;
  if (heading) context.page_heading = heading;
  return context;
};
