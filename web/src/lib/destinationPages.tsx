import { lazy } from "react";

export const HomePage = lazy(() => import("../pages/DatalakeHealth").then((m) => ({ default: m.DatalakeHealth })));
export const TablesPage = lazy(() => import("../pages/Catalog").then((m) => ({ default: m.Catalog })));
export const RunsPage = lazy(() =>
  import("../pages/operations/OperationsHome").then((m) => ({ default: m.OperationsHome }))
);
export const DecisionsPage = lazy(() =>
  import("../pages/decisions/DecisionsPage").then((m) => ({ default: m.DecisionsPage }))
);
export const InsightsPage = lazy(() => import("../pages/insights/InsightsPage").then((m) => ({ default: m.InsightsPage })));
export const SettingsPage = lazy(() =>
  import("../pages/settings/SettingsLayout").then((m) => ({ default: m.SettingsLayout }))
);
