import { lazy, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { AttentionTab } from "../pages/governance/AttentionTab";
import { InventoryTab } from "../pages/governance/InventoryTab";

import { resolveLegacyRedirect, type AppLocation } from "../lib/routes";

const Catalog = lazy(() => import("../pages/Catalog").then((m) => ({ default: m.Catalog })));

const SearchResults = lazy(() => import("../pages/SearchResults").then((m) => ({ default: m.SearchResults })));
const OperationsHome = lazy(() =>
  import("../pages/operations/OperationsHome").then((m) => ({ default: m.OperationsHome }))
);
const Cost = lazy(() => import("../pages/Cost").then((m) => ({ default: m.Cost })));
const DecisionsPage = lazy(() =>
  import("../pages/decisions/DecisionsPage").then((m) => ({ default: m.DecisionsPage }))
);
const InsightsPage = lazy(() => import("../pages/insights/InsightsPage").then((m) => ({ default: m.InsightsPage })));

const SEARCH_GROUPS = new Set(["catalogs", "schemas", "tables", "columns"]);

const toTarget = (location: AppLocation) => ({
  pathname: location.pathname,
  search: location.search,
  hash: location.hash
});

export const LegacyRedirect = () => {
  const location = useLocation();
  const target = resolveLegacyRedirect(location);
  if (!target) return <Navigate to="/" replace />;
  return <Navigate to={toTarget(target)} replace />;
};

export const Canonical = ({ children }: { children: ReactNode }) => {
  const location = useLocation();
  const target = resolveLegacyRedirect(location);
  if (!target) return children;
  return <Navigate to={toTarget(target)} replace />;
};

const TablesBody = () => {
  const { search } = useLocation();
  const params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  const view = params.get("view");
  const notice = params.get("notice");
  const group = params.get("group");

  if (view === "unused" || view === "freshness" || view === "maintenance-age" || view === "p95-latency") {
    return <Catalog />;
  }
  if (view === "scan-failed" || notice === "governance-triage" || notice === "governance-unknown") {
    return (
      <>
        <AttentionTab />
      </>
    );
  }
  if (notice === "governance-ownership") {
    return (
      <>
        <InventoryTab />
      </>
    );
  }
  if (notice === "search" || (group != null && SEARCH_GROUPS.has(group))) return <SearchResults />;
  return <Catalog />;
};

export const TablesHome = () => (
  <Canonical>
    <TablesBody />
  </Canonical>
);

const RunsBody = () => <OperationsHome />;

export const RunsHome = () => (
  <Canonical>
    <RunsBody />
  </Canonical>
);

const DecisionsBody = () => {
  const { search } = useLocation();
  const params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  return (
    <>
      <DecisionsPage />
    </>
  );
};

export const DecisionsHome = () => (
  <Canonical>
    <DecisionsBody />
  </Canonical>
);

const CostBody = () => <Cost />;

export const CostHome = () => (
  <Canonical>
    <CostBody />
  </Canonical>
);

const InsightsBody = () => <InsightsPage />;

export const InsightsHome = () => (
  <Canonical>
    <InsightsBody />
  </Canonical>
);

