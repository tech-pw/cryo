import { lazy } from "react";
import { Link, Navigate, useSearchParams } from "react-router-dom";

import { useAuth } from "../../lib/auth";
import { SettingsLayout } from "./SettingsLayout";

const Connections = lazy(() => import("../Connections").then((m) => ({ default: m.Connections })));
const Skillsets = lazy(() => import("../Skillsets").then((m) => ({ default: m.Skillsets })));
const PoliciesTab = lazy(() => import("../governance/PoliciesTab").then((m) => ({ default: m.PoliciesTab })));
const AccessControl = lazy(() => import("./AccessControl").then((m) => ({ default: m.AccessControl })));
const AiAccessTable = lazy(() => import("./AiAccessTable").then((m) => ({ default: m.AiAccessTable })));

const useSettingsTab = () => useSearchParams()[0].get("tab");

export const SettingsConnectionsHome = () => (
  <SettingsLayout>
    <Connections embedded />
  </SettingsLayout>
);

export const SettingsAiHome = () => {
  const { role } = useAuth();
  const tab = useSettingsTab();
  if (tab === "skillsets") {
    if (role !== "admin") return <Navigate to="/settings/ai" replace />;
    return (
      <SettingsLayout>
        <Skillsets />
      </SettingsLayout>
    );
  }
  return (
    <SettingsLayout>
      <AiAccessTable />
      <p className="mt-3 text-sm text-muted-foreground">
        <Link to="/settings/ai/models" className="text-primary underline-offset-2 hover:underline">
          AI model settings
        </Link>{" "}
        control Ask Cryo, tiers, and API keys. Experimental models are labelled experimental.
      </p>
    </SettingsLayout>
  );
};

export const SettingsAccessHome = () => (
  <SettingsLayout>
    <AccessControl />
    <div className="mt-8">
      <PoliciesTab />
    </div>
  </SettingsLayout>
);

export const SettingsAdminHome = () => {
  const { role } = useAuth();
  if (role !== "admin") return <Navigate to="/settings/connections" replace />;
  return (
    <SettingsLayout>
      <Connections embedded forcedTab="ops" />
    </SettingsLayout>
  );
};

export const SettingsNotificationsHome = () => (
  <SettingsLayout>
    <p className="mb-3 text-sm text-muted-foreground">
      Slack daily report is edited on the Slack connection. This page does not store a second webhook.
    </p>
    <Connections embedded forcedTab="slack" />
  </SettingsLayout>
);
