import { useQuery } from "@tanstack/react-query";

import {
  getDetectionTiers,
  getSmartMaintenanceOverview,
  listSmartLayoutRecommendations
} from "../api/client";
import { useAuth } from "../lib/auth";

export function useSmartMaintenanceMetrics() {
  const { api, token } = useAuth();
  const overview = useQuery({
    queryKey: ["smart-maintenance-overview", token],
    queryFn: () => getSmartMaintenanceOverview(api)
  });
  const tiers = useQuery({
    queryKey: ["smart-maintenance-detection-tiers", token],
    queryFn: () => getDetectionTiers(api, 200)
  });
  const recommendations = useQuery({
    queryKey: ["smart-maintenance-layout-recommendations", token],
    queryFn: () => listSmartLayoutRecommendations(api)
  });
  return { overview, tiers, recommendations };
}
