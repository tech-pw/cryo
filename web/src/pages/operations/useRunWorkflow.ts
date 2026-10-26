import { useQuery } from "@tanstack/react-query";

import { listPresets } from "../../api/client";
import type { PresetRead, RunRead } from "../../api/types";
import { useAuth } from "../../lib/auth";
import { isScanRun, summaryText, targetLabel } from "../../lib/operations";
import { NIGHTLY_WORKFLOW_NAME } from "./nightlyBatch";

export interface RunWorkflow {
  name: string;
  link: string;
}

/** The workflow a run belongs to, for breadcrumbs: the preset name for scheduled runs, not the run kind. */
export const runWorkflow = (run: RunRead | undefined, presets: PresetRead[] | undefined): RunWorkflow => {
  if (!run) return { name: "Workflow", link: "/runs" };
  if (isScanRun(run)) return { name: "Scanner", link: "/runs/scanner" };
  if (run.preset_id) {
    const preset = presets?.find((item) => item.id === run.preset_id);
    return { name: preset?.name ?? "Workflow", link: `/runs/workflows/${run.preset_id}` };
  }
  if (summaryText(run.summary, "pipeline") === "batch") return { name: NIGHTLY_WORKFLOW_NAME, link: "/runs" };
  return { name: targetLabel(run), link: "/runs" };
};

export const useRunWorkflow = (run: RunRead | undefined): RunWorkflow => {
  const { api, token } = useAuth();
  const presets = useQuery({
    queryKey: ["presets", token],
    queryFn: () => listPresets(api),
    enabled: Boolean(run?.preset_id),
    staleTime: 60_000
  });
  return runWorkflow(run, presets.data);
};
