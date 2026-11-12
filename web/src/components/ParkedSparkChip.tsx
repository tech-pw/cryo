import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { lookupParkedJobs, parkedDetail, type ParkedJob } from "../api/asyncSpark";
import { useAuth } from "../lib/auth";
import { Badge } from "./ui/badge";

/** Parked async Spark jobs among these running jobs, keyed by job id (#491). */
export const useParkedJobs = (runningJobIds: string[]): Map<string, ParkedJob> => {
  const { api, token } = useAuth();
  const ids = [...runningJobIds].sort();
  const parked = useQuery({
    queryKey: ["parked-jobs", token, ids],
    queryFn: () => lookupParkedJobs(api, ids),
    enabled: ids.length > 0,
    refetchInterval: 30_000,
    retry: false
  });
  return useMemo(() => new Map((parked.data?.jobs ?? []).map((job) => [job.job_id, job])), [parked.data]);
};

export const ParkedSparkChip = ({ job }: { job: ParkedJob }) => (
  <Badge variant={job.overdue ? "warning" : "outline"} title={parkedDetail(job)}>
    {job.overdue ? "On Spark · not checked" : "On Spark (parked)"}
  </Badge>
);
