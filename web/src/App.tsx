import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Suspense, lazy, type ReactNode } from "react";
import { Route, Routes, useLocation } from "react-router-dom";

import { AppShellLayout } from "./AppShellLayout";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { NotificationProvider } from "./components/NotificationRegion";
import {
  CostHome,
  DecisionsHome,
  InsightsHome,
  LegacyRedirect,
  RunsHome,
  TablesHome
} from "./components/Redirects";
import {
  SettingsAboutHome,
  SettingsAccessHome,
  SettingsAdminHome,
  SettingsAiHome,
  SettingsConnectionsHome,
  SettingsNotificationsHome
} from "./pages/settings/homes";
import { SettingsLayout } from "./pages/settings/SettingsLayout";
import { Skeleton } from "./components/ui/skeleton";
import { Toaster } from "./components/ui/sonner";
import { AuthProvider, useAuth } from "./lib/auth";
import { ThemeProvider } from "./lib/themeContext";
import { CryoTooltipProvider } from "./components/ui/tooltip";
import { RUNS_NESTED_PATHS } from "./lib/routes";

const BatchReviewPage = lazy(() =>
  import("./components/batch/BatchReviewPage").then((m) => ({ default: m.BatchReviewPage }))
);
const JobDetail = lazy(() => import("./pages/JobDetail").then((m) => ({ default: m.JobDetail })));
const CostAttribution = lazy(() =>
  import("./pages/CostAttribution").then((m) => ({ default: m.CostAttribution }))
);
const DatalakeHealth = lazy(() => import("./pages/DatalakeHealth").then((m) => ({ default: m.DatalakeHealth })));
const Login = lazy(() => import("./pages/Login").then((m) => ({ default: m.Login })));
const LoginCallback = lazy(() => import("./pages/LoginCallback").then((m) => ({ default: m.LoginCallback })));
const RunDetail = lazy(() => import("./pages/operations/RunDetail").then((m) => ({ default: m.RunDetail })));
const PresetDetail = lazy(() =>
  import("./pages/operations/PresetDetail").then((m) => ({ default: m.PresetDetail }))
);
const ScannerDetail = lazy(() =>
  import("./pages/operations/ScannerDetail").then((m) => ({ default: m.ScannerDetail }))
);
const Styleguide = lazy(() => import("./pages/dev/Styleguide"));
const TableDetail = lazy(() => import("./pages/TableDetail").then((m) => ({ default: m.TableDetail })));
const NotFound = lazy(() => import("./pages/NotFound").then((m) => ({ default: m.NotFound })));
const AssistantApprove = lazy(() =>
  import("./pages/AssistantApprove").then((m) => ({ default: m.AssistantApprove }))
);
const AiModelsSettings = lazy(() =>
  import("./pages/settings/AiModelsSettings").then((m) => ({ default: m.AiModelsSettings }))
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

const runsNestedElement: Record<(typeof RUNS_NESTED_PATHS)[number], ReactNode> = {
  "/runs/jobs/:jobId": <JobDetail />,
  "/runs/workflows/:presetId": <PresetDetail />,
  "/runs/scanner": <ScannerDetail />,
  "/runs/batch": <BatchReviewPage />,
  "/runs/:runId": <RunDetail />
};

const AuthenticatedRoutes = () => (
  <AppShellLayout>
    <Suspense fallback={<PageFallback />}>
      <ErrorBoundary>
        <Routes>
          <Route path="/" element={<DatalakeHealth />} />
          <Route path="/health" element={<LegacyRedirect />} />
          <Route path="/tables" element={<TablesHome />} />
          <Route path="/tables/:tableId" element={<TableDetail />} />
          <Route path="/catalogs" element={<LegacyRedirect />} />
          <Route path="/search" element={<LegacyRedirect />} />
          <Route path="/dark-tables" element={<LegacyRedirect />} />
          <Route path="/sla" element={<LegacyRedirect />} />
          <Route path="/runs" element={<RunsHome />} />
          {RUNS_NESTED_PATHS.map((path) => (
            <Route key={path} path={path} element={runsNestedElement[path]} />
          ))}
          <Route path="/operations" element={<LegacyRedirect />} />
          <Route path="/operations/scanner" element={<LegacyRedirect />} />
          <Route path="/operations/presets/:presetId" element={<LegacyRedirect />} />
          <Route path="/operations/runs/:runId" element={<LegacyRedirect />} />
          <Route path="/scans" element={<LegacyRedirect />} />
          <Route path="/scans/:runId" element={<LegacyRedirect />} />
          <Route path="/presets" element={<LegacyRedirect />} />
          <Route path="/jobs/:jobId" element={<LegacyRedirect />} />
          <Route path="/control-room" element={<LegacyRedirect />} />
          <Route path="/decisions" element={<DecisionsHome />} />
          <Route path="/smart-maintenance" element={<LegacyRedirect />} />
          <Route path="/optimize" element={<LegacyRedirect />} />
          <Route path="/cost" element={<CostHome />} />
          <Route path="/impact" element={<LegacyRedirect />} />
          <Route path="/scan-cost" element={<LegacyRedirect />} />
          <Route path="/cost/aws" element={<LegacyRedirect />} />
          <Route path="/aws" element={<LegacyRedirect />} />
          <Route path="/attribution" element={<CostAttribution />} />
          <Route path="/insights" element={<InsightsHome />} />
          <Route path="/similarity" element={<LegacyRedirect />} />
          <Route path="/anomalies" element={<LegacyRedirect />} />
          <Route path="/queries" element={<LegacyRedirect />} />
          <Route path="/governance" element={<LegacyRedirect />} />
          <Route path="/settings" element={<LegacyRedirect />} />
          <Route path="/settings/connections" element={<SettingsConnectionsHome />} />
          <Route path="/settings/ai" element={<SettingsAiHome />} />
          <Route path="/settings/ai/models" element={<SettingsLayout><AiModelsSettings /></SettingsLayout>} />
          <Route path="/assistant/approve/:proposalId" element={<AssistantApprove />} />
          <Route path="/settings/access" element={<SettingsAccessHome />} />
          <Route path="/settings/admin" element={<SettingsAdminHome />} />
          <Route path="/settings/notifications" element={<SettingsNotificationsHome />} />
          <Route path="/settings/about" element={<SettingsAboutHome />} />
          <Route path="/connections" element={<LegacyRedirect />} />
          <Route path="/skillsets" element={<LegacyRedirect />} />
          <Route path="/ai" element={<LegacyRedirect />} />
          {import.meta.env.DEV ? <Route path="/dev/styleguide" element={<Styleguide />} /> : null}
          <Route path="*" element={<NotFound />} />
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
