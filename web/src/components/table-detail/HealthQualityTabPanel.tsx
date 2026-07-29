import { AiAnalyticsTab } from "./AiAnalyticsTab";
import { PropertyRecommendations } from "./PropertyRecommendations";
import { QualityTab } from "./QualityTab";

export const HealthQualityTabPanel = () => (
  <div className="td-tab-content">
    <PropertyRecommendations />
    <QualityTab />
    <div style={{ marginTop: 16 }}>
      <AiAnalyticsTab />
    </div>
  </div>
);
