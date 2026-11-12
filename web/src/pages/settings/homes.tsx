import { lazy } from "react";
import { Link, Navigate, useSearchParams } from "react-router-dom";

import { Alert } from "../../components/ui/alert";
import { useAuth } from "../../lib/auth";
import { SettingsLayout } from "./SettingsLayout";

const Connections = lazy(() => import("../Connections").then((m) => ({ default: m.Connections })));
const Skillsets = lazy(() => import("../Skillsets").then((m) => ({ default: m.Skillsets })));
const PoliciesTab = lazy(() => import("../governance/PoliciesTab").then((m) => ({ default: m.PoliciesTab })));
const AccessControl = lazy(() => import("./AccessControl").then((m) => ({ default: m.AccessControl })));
const AiAccessTable = lazy(() => import("./AiAccessTable").then((m) => ({ default: m.AiAccessTable })));
const About = lazy(() => import("./About").then((m) => ({ default: m.About })));

const useSettingsTab = () => useSearchParams()[0].get("tab");

export const SettingsConnectionsHome = () => (
  <SettingsLayout
    title="Connections"
    subtitle="Manage your Iceberg catalogs, maintenance engines, and integrations."
  >
    <Connections embedded />
  </SettingsLayout>
);

export const SettingsAiHome = () => {
  const { role } = useAuth();
  const tab = useSettingsTab();
  if (tab === "skillsets") {
    if (role !== "admin") return <Navigate to="/settings/ai" replace />;
    return (
      <SettingsLayout title="Skillsets" subtitle="Reusable instructions Ask Cryo can follow.">
        <Skillsets />
      </SettingsLayout>
    );
  }
  return (
    <SettingsLayout title="AI & models" subtitle="Who may call the model, and through which provider.">
      <AiAccessTable />
      <p className="settings-note mt-3 text-sm text-muted-foreground">
        <Link to="/settings/ai/models" className="text-primary underline underline-offset-2">
          AI model settings
        </Link>{" "}
        control Ask Cryo, tiers, and API keys. Experimental models are labelled experimental.
      </p>
    </SettingsLayout>
  );
};

export const SettingsAccessHome = () => (
  <SettingsLayout title="Access & policy" subtitle="Who can see and change what, and which policies apply.">
    <AccessControl />
    <div className="mt-8">
      <PoliciesTab />
    </div>
  </SettingsLayout>
);

export const SettingsAdminHome = () => {
  const { role } = useAuth();
  if (role !== "admin") {
    return (
      <SettingsLayout title="Admin" subtitle="Operational controls for this Cryo install.">
        <Alert
          variant="info"
          title="Admin role required"
          description={`Your role is ${role ?? "unknown"}. Ask an admin to open this page or to change your role.`}
        />
      </SettingsLayout>
    );
  }
  return (
    <SettingsLayout title="Admin" subtitle="Operational controls for this Cryo install.">
      <Connections embedded forcedTab="ops" />
    </SettingsLayout>
  );
};

export const SettingsNotificationsHome = () => (
  <SettingsLayout
    title="Notifications"
    subtitle="Configure Slack delivery and generate operational reports."
  >
    <Connections embedded forcedTab="slack" />
  </SettingsLayout>
);

export const SettingsAboutHome = () => (
  <SettingsLayout title="About" subtitle="Which Cryo build is running, for upgrades and bug reports.">
    <About />
  </SettingsLayout>
);
