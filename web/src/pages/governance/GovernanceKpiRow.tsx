import { useQuery } from "@tanstack/react-query";
import { getGitOpsStatus, getS3TieringRecommendations, listFleetAnomalies, listPendingApprovals } from "../../api/client";
import { MetricTile } from "../../components/ui/stat";
import { useAuth } from "../../lib/auth";

export const GovernanceKpiRow = () => {
  const { api } = useAuth();

  const approvals = useQuery({
    queryKey: ["pending-approvals", api.token],
    queryFn: () => listPendingApprovals(api),
    refetchInterval: 30_000
  });
  const anomalies = useQuery({
    queryKey: ["fleet-anomalies", api.token],
    queryFn: () => listFleetAnomalies(api),
    refetchInterval: 60_000
  });
  const s3Tiering = useQuery({
    queryKey: ["s3-tiering-recommendations", api.token],
    queryFn: () => getS3TieringRecommendations(api, { min_size_gb: 50 }),
    retry: false
  });
  const gitopsStatus = useQuery({
    queryKey: ["gitops-status", api.token],
    queryFn: () => getGitOpsStatus(api),
    refetchInterval: 60_000
  });

  const tierCandidateCount = s3Tiering.data?.recommendations.length ?? 0;
  const policyViolationCount = gitopsStatus.data?.conflicts.length ?? 0;

  return (
    <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <MetricTile
        label="Pending approvals"
        value={approvals.data?.length ?? 0}
        loading={approvals.isLoading}
        emphasis={(approvals.data?.length ?? 0) > 0}
      />
      <MetricTile
        label="Open anomalies"
        value={anomalies.data?.length ?? 0}
        loading={anomalies.isLoading}
        emphasis={(anomalies.data?.length ?? 0) > 0}
      />
      <MetricTile
        label="Tier candidates"
        value={tierCandidateCount}
        loading={s3Tiering.isLoading}
      />
      <MetricTile
        label="Policy violations"
        value={policyViolationCount}
        loading={gitopsStatus.isLoading}
        emphasis={policyViolationCount > 0}
      />
    </div>
  );
};
