import { Database, Heart, History, Share2, Shield } from "lucide-react";
import { CostTabLabel } from "./CostTab";

const tabIconClass = "mr-1 inline size-3.5";

export const OverviewTabLabel = () => (
  <span>
    <Heart className={tabIconClass} />
    Overview
  </span>
);

export const StructureTabLabel = () => (
  <span>
    <Database className={tabIconClass} />
    Structure
  </span>
);

export const HealthQualityTabLabel = () => (
  <span>
    <Shield className={tabIconClass} />
    Health &amp; Quality
  </span>
);

export const LineageTabLabel = () => (
  <span>
    <Share2 className={tabIconClass} />
    Lineage
  </span>
);

export const ActivityTabLabel = () => (
  <span>
    <History className={tabIconClass} />
    Activity
  </span>
);

export { CostTabLabel };
