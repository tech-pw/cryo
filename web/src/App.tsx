import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Suspense, lazy } from "react";
import { Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";

import { AppShellLayout } from "./AppShellLayout";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { NotificationProvider } from "./components/NotificationRegion";
import { Skeleton } from "./components/ui/skeleton";
import { Toaster } from "./components/ui/sonner";
import { AuthProvider, useAuth } from "./lib/auth";
import { ThemeProvider } from "./lib/themeContext";
import { CryoTooltipProvider } from "./components/ui/tooltip";

const Connections = lazy(() => import("./pages/Connections").then((m) => ({ default: m.Connections })));
const Skillsets = lazy(() => import("./pages/Skillsets").then((m) => ({ default: m.Skillsets })));
const JobDetail = lazy(() => import("./pages/JobDetail").then((m) => ({ default: m.JobDetail })));
const CostAttribution = lazy(() =>
  import("./pages/CostAttribution").then((m) => ({ default: m.CostAttribution }))
);
const Cost = lazy(() => import("./pages/Cost").then((m) => ({ default: m.Cost })));
const DatalakeHealth = lazy(() => import("./pages/DatalakeHealth").then((m) => ({ default: m.DatalakeHealth })));
const Governance = lazy(() => import("./pages/Governance").then((m) => ({ default: m.Governance })));
const Intelligence = lazy(() => import("./pages/Intelligence").then((m) => ({ default: m.Intelligence })));
const Login = lazy(() => import("./pages/Login").then((m) => ({ default: m.Login })));
const LoginCallback = lazy(() => import("./pages/LoginCallback").then((m) => ({ default: m.LoginCallback })));
const Catalog = lazy(() => import("./pages/Catalog").then((m) => ({ default: m.Catalog })));
const OperationsHome = lazy(() =>
  import("./pages/operations/OperationsHome").then((m) => ({ default: m.OperationsHome }))
);
const RunDetail = lazy(() => import("./pages/operations/RunDetail").then((m) => ({ default: m.RunDetail })));
const PresetDetail = lazy(() =>
  import("./pages/operations/PresetDetail").then((m) => ({ default: m.PresetDetail }))
);
const ScannerDetail = lazy(() =>
  import("./pages/operations/ScannerDetail").then((m) => ({ default: m.ScannerDetail }))
);
const QueryPerformance = lazy(() =>
  import("./pages/QueryPerformance").then((m) => ({ default: m.QueryPerformance }))
);
const SLAStatus = lazy(() => import("./pages/SLAStatus").then((m) => ({ default: m.SLAStatus })));
const SearchResults = lazy(() =>
  import("./pages/SearchResults").then((m) => ({ default: m.SearchResults }))
);
const Styleguide = lazy(() => import("./pages/dev/Styleguide"));
const TableDetail = lazy(() => import("./pages/TableDetail").then((m) => ({ default: m.TableDetail })));
const SmartMaintenance = lazy(() =>
  import("./pages/SmartMaintenance").then((m) => ({ default: m.SmartMaintenance }))
);
const ControlRoom = lazy(() =>
  import("./pages/ControlRoom").then((m) => ({ default: m.ControlRoom }))
);

const PageFallback = () => (
  <div className="flex min-h-[240px] flex-col items-center justify-center gap-3 p-6">
    <Skeleton className="h-8 w-48" />
    <Skeleton className="h-4 w-64" />
    <Skeleton className="h-4 w-56" />
  </div>
);

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      retry: 1
    }
  }
});

const TablesListRedirect = () => {
  const { search } = useLocation();
  return <Navigate to={`/catalogs${search}`} replace />;
};

const RunsRunRedirect = () => {
  const { runId } = useParams();
  return <Navigate to={`/operations/runs/${runId}`} replace />;
};

const ScansRunRedirect = () => {
  const { runId } = useParams();
  return <Navigate to={`/operations/runs/${runId}`} replace />;
};

const SkillsetsRoute = () => {
  const { role } = useAuth();
  if (role !== "admin") {
    return <Navigate to="/health" replace />;
  }
  return <Skillsets />;
};

const AuthenticatedRoutes = () => (
  <AppShellLayout>
    <Suspense fallback={<PageFallback />}>
      <ErrorBoundary>
        <Routes>
            <Route path="/" element={<Navigate to="/health" replace />} />
            <Route path="/health" element={<DatalakeHealth />} />
            <Route path="/catalogs" element={<Catalog />} />
            <Route path="/cost" element={<Cost />} />
            <Route path="/cost/aws" element={<Navigate to="/cost?tab=aws-integrations" replace />} />
            <Route path="/aws" element={<Navigate to="/cost?tab=aws-integrations" replace />} />
            <Route path="/attribution" element={<CostAttribution />} />
            <Route path="/tables" element={<TablesListRedirect />} />
            <Route path="/tables/:tableId" element={<TableDetail />} />
            <Route path="/scans" element={<Navigate to="/operations/scanner" replace />} />
            <Route path="/scans/:runId" element={<ScansRunRedirect />} />
            <Route path="/operations" element={<OperationsHome />} />
            <Route path="/operations/scanner" element={<ScannerDetail />} />
            <Route path="/operations/presets/:presetId" element={<PresetDetail />} />
            <Route path="/operations/runs/:runId" element={<RunDetail />} />
            <Route path="/ai" element={<Intelligence />} />
            <Route path="/governance" element={<Governance />} />
            <Route path="/queries" element={<QueryPerformance />} />
            <Route path="/sla" element={<SLAStatus />} />
            <Route path="/search" element={<SearchResults />} />
            <Route path="/optimize" element={<Navigate to="/smart-maintenance" replace />} />
            <Route path="/smart-maintenance" element={<SmartMaintenance />} />
            <Route path="/control-room" element={<ControlRoom />} />
            <Route path="/presets" element={<Navigate to="/operations" replace />} />
            <Route path="/runs" element={<Navigate to="/operations?tab=runs" replace />} />
            <Route path="/runs/:runId" element={<RunsRunRedirect />} />
            <Route path="/jobs/:jobId" element={<JobDetail />} />
            <Route path="/connections" element={<Connections />} />
            <Route path="/skillsets" element={<SkillsetsRoute />} />
            {import.meta.env.DEV ? <Route path="/dev/styleguide" element={<Styleguide />} /> : null}
          </Routes>
        </ErrorBoundary>
      </Suspense>
    </AppShellLayout>
);

const Shell = () => {
  const location = useLocation();
  const { loading: authLoading, isAuthenticated } = useAuth();

  if (authLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--color-layout-bg)] p-6">
        <p className="text-sm text-muted-foreground">Restoring session…</p>
      </div>
    );
  }

  if (!isAuthenticated) {
    if (location.pathname === "/login/callback") {
      return (
        <Suspense fallback={<PageFallback />}>
          <LoginCallback />
        </Suspense>
      );
    }
    return (
      <Suspense fallback={<PageFallback />}>
        <Login />
      </Suspense>
    );
  }

  return <AuthenticatedRoutes />;
};

export const App = () => (
  <QueryClientProvider client={queryClient}>
    <ThemeProvider>
      <CryoTooltipProvider>
        <AuthProvider>
          <NotificationProvider>
            <Shell />
            <Toaster />
          </NotificationProvider>
        </AuthProvider>
      </CryoTooltipProvider>
    </ThemeProvider>
  </QueryClientProvider>
);
