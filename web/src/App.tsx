import {
  ApiOutlined,
  AppstoreOutlined,
  BarChartOutlined,
  ClockCircleOutlined,
  CloudOutlined,
  ControlOutlined,
  DatabaseOutlined,
  DollarOutlined,
  ExperimentOutlined,
  HistoryOutlined,
  MoonOutlined,
  SettingOutlined,
  RobotOutlined,
  SunOutlined,
  SyncOutlined,
  TeamOutlined,
  ThunderboltOutlined,
  MenuOutlined
} from "@ant-design/icons";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Button, ConfigProvider, Layout, Menu, Space, Spin, Tooltip, Typography, theme, Drawer } from "antd";
import type { MenuProps } from "antd";
import { Suspense, lazy, useMemo, useState } from "react";
import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AuthProvider, useAuth } from "./lib/auth";
import { cryoColorsLight, cryoColorsDark } from "./lib/theme";
import { ThemeProvider, useTheme } from "./lib/themeContext";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { NoSourcesBanner } from "./components/NoSourcesBanner";
import { NotificationProvider } from "./components/NotificationRegion";

const Connections = lazy(() => import("./pages/Connections").then((m) => ({ default: m.Connections })));
const Skillsets = lazy(() => import("./pages/Skillsets").then((m) => ({ default: m.Skillsets })));
const CostAttribution = lazy(() =>
  import("./pages/CostAttribution").then((m) => ({ default: m.CostAttribution }))
);
const CostInsights = lazy(() =>
  import("./pages/CostInsights").then((m) => ({ default: m.CostInsights }))
);
const Cost = lazy(() => import("./pages/Cost").then((m) => ({ default: m.Cost })));
const DatalakeHealth = lazy(() => import("./pages/DatalakeHealth").then((m) => ({ default: m.DatalakeHealth })));
const Governance = lazy(() => import("./pages/Governance").then((m) => ({ default: m.Governance })));
const Intelligence = lazy(() => import("./pages/Intelligence").then((m) => ({ default: m.Intelligence })));
const Login = lazy(() => import("./pages/Login").then((m) => ({ default: m.Login })));
const LoginCallback = lazy(() => import("./pages/LoginCallback").then((m) => ({ default: m.LoginCallback })));
const LayoutOptimization = lazy(() => import("./pages/LayoutOptimization"));
const Catalog = lazy(() => import("./pages/Catalog").then((m) => ({ default: m.Catalog })));
const Presets = lazy(() => import("./pages/Presets").then((m) => ({ default: m.Presets })));
const QueryPerformance = lazy(() =>
  import("./pages/QueryPerformance").then((m) => ({ default: m.QueryPerformance }))
);
const Runs = lazy(() => import("./pages/Runs").then((m) => ({ default: m.Runs })));
const SLAStatus = lazy(() => import("./pages/SLAStatus").then((m) => ({ default: m.SLAStatus })));
const Scans = lazy(() => import("./pages/Scans").then((m) => ({ default: m.Scans })));
const TableDetail = lazy(() => import("./pages/TableDetail").then((m) => ({ default: m.TableDetail })));

const PageFallback = () => (
  <div className="content" style={{ display: "grid", placeItems: "center", minHeight: 240 }}>
    <Spin size="large" />
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

// Cost attribution is intentionally not in the nav: per docs/OSS_READINESS.md §2.2 the
// feature is a stub that requires custom `cost_center` / `department` tags on Source /
// Connection models and isn't ready for the v1 launch surface. The /attribution route
// remains registered so the URL still works for direct links from docs / future
// re-introduction, but no entry point exists in the navigation.
const navItems = [
  { key: "/health", icon: <BarChartOutlined />, label: <Link to="/health">Health</Link> },
  { key: "/catalogs", icon: <AppstoreOutlined />, label: <Link to="/catalogs">Catalogs</Link> },
  { key: "/scans", icon: <SyncOutlined />, label: <Link to="/scans">Scanner</Link> },
  { key: "/ai", icon: <RobotOutlined />, label: <Link to="/ai">Intelligence</Link> },
  { key: "/governance", icon: <TeamOutlined />, label: <Link to="/governance">Governance</Link> },
  { key: "/queries", icon: <ThunderboltOutlined />, label: <Link to="/queries">Queries</Link> },
  { key: "/cost", icon: <DollarOutlined />, label: <Link to="/cost">Cost</Link> },
  { key: "/sla", icon: <ClockCircleOutlined />, label: <Link to="/sla">SLA</Link> },
  { key: "/optimize", icon: <ExperimentOutlined />, label: <Link to="/optimize">Optimize</Link> },
  { key: "/presets", icon: <ControlOutlined />, label: <Link to="/presets">Presets</Link> },
  { key: "/runs", icon: <HistoryOutlined />, label: <Link to="/runs">Runs</Link> },
  { key: "/connections", icon: <ApiOutlined />, label: <Link to="/connections">Connections</Link> }
];

// Index lookup is fragile to nav reordering — resolve by route key instead.
const navByKey = Object.fromEntries(navItems.map((item) => [item.key, item]));

const navGroups: MenuProps["items"] = [
  {
    type: "group",
    label: "Overview",
    children: [navByKey["/health"], navByKey["/sla"]]
  },
  {
    type: "group",
    label: "Inventory",
    children: [navByKey["/catalogs"], navByKey["/scans"]]
  },
  {
    type: "group",
    label: "Operations",
    children: [navByKey["/optimize"], navByKey["/presets"], navByKey["/runs"]]
  },
  {
    type: "group",
    label: "Insights",
    children: [
      navByKey["/ai"],
      navByKey["/governance"],
      navByKey["/queries"],
      navByKey["/cost"],
      navByKey["/cost-insights"]
    ]
  },
  {
    type: "group",
    label: "Settings",
    children: [navByKey["/connections"]]
  }
];

const routeSelectionAliases = new Map<string, string>();

const ThemeToggle = () => {
  const { theme, toggleTheme } = useTheme();
  return (
    <Tooltip title={theme === "light" ? "Switch to dark mode" : "Switch to light mode"}>
      <Button
        type="text"
        icon={theme === "light" ? <MoonOutlined /> : <SunOutlined />}
        onClick={toggleTheme}
        aria-label="Toggle theme"
      />
    </Tooltip>
  );
};

const Shell = () => {
  const location = useLocation();
  const { subject, role, loading: authLoading, error: authError, isAuthenticated, signOut } = useAuth();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const selectedKey = useMemo(() => {
    const alias = [...routeSelectionAliases.entries()].find(([route]) => location.pathname.startsWith(route))?.[1];
    return alias ?? navItems.find((item) => location.pathname.startsWith(item.key))?.key ?? "/health";
  }, [location.pathname]);

  if (authLoading) {
    return (
      <Layout className="app-shell">
        <Layout.Content className="content login-screen">
          <Typography.Text type="secondary">Restoring session…</Typography.Text>
        </Layout.Content>
      </Layout>
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

  return (
    <Layout className="app-shell">
      <a href="#main-content" className="skip-link">
        Skip to main content
      </a>
      <Layout.Sider width={212} className="side-nav" breakpoint="lg" collapsedWidth={0}>
        <div className="brand">
          <SettingOutlined />
          <span>Cryo</span>
        </div>
        <Menu mode="inline" selectedKeys={[selectedKey]} items={navGroups} />
      </Layout.Sider>
      <Layout>
        <Layout.Header className="topbar">
          <div style={{ display: "flex", alignItems: "center" }}>
            <Button
              type="text"
              icon={<MenuOutlined />}
              onClick={() => setMobileMenuOpen(true)}
              className="mobile-menu-btn"
              aria-label="Open navigation menu"
            />
            <Typography.Text type="secondary">Apache Iceberg maintenance and health</Typography.Text>
          </div>
          <Space>
            <ThemeToggle />
            {authError ? (
              <Typography.Text type="danger" style={{ maxWidth: 280 }}>
                Auth: {authError}
              </Typography.Text>
            ) : null}
            <Typography.Text type="secondary">
              {subject} · {role}
            </Typography.Text>
            <Button size="small" onClick={signOut}>
              Sign out
            </Button>
          </Space>
        </Layout.Header>
        <Layout.Content className="content" id="main-content" tabIndex={-1}>
          <NoSourcesBanner />
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
              <Route
                path="/tables"
                element={<Navigate to="/catalogs" replace />}
              />
              <Route path="/tables/:tableId" element={<TableDetail />} />
              <Route path="/scans" element={<Scans />} />
              <Route path="/scans/:runId" element={<Scans />} />
              <Route path="/ai" element={<Intelligence />} />
              <Route path="/governance" element={<Governance />} />
              <Route path="/queries" element={<QueryPerformance />} />
              <Route path="/sla" element={<SLAStatus />} />
              <Route path="/optimize" element={<LayoutOptimization />} />
              <Route path="/presets" element={<Presets />} />
              <Route path="/runs" element={<Runs />} />
              <Route path="/runs/:runId" element={<Runs />} />
              <Route path="/jobs/:jobId" element={<Navigate to="/runs/:jobId" replace />} />
              <Route path="/connections" element={<Connections />} />
              <Route path="/skillsets" element={<Skillsets />} />
              <Route path="/cost-insights" element={<CostInsights />} />
            </Routes>
            </ErrorBoundary>
          </Suspense>
        </Layout.Content>
      </Layout>
      <Drawer
        title={
          <div className="brand" style={{ padding: 0, height: "auto" }}>
            <SettingOutlined />
            <span>Cryo</span>
          </div>
        }
        placement="left"
        onClose={() => setMobileMenuOpen(false)}
        open={mobileMenuOpen}
        size={220}
        styles={{
          body: { padding: 0, background: "var(--sidebar-bg)" },
          header: { background: "var(--sidebar-bg)", borderBottom: "1px solid #202a38" }
        }}
        closeIcon={<MenuOutlined style={{ color: "var(--sidebar-text-active)" }} />}
      >
        <Menu
          mode="inline"
          selectedKeys={[selectedKey]}
          items={navGroups}
          onClick={() => setMobileMenuOpen(false)}
        />
      </Drawer>
    </Layout>
  );
};

const ThemedApp = () => {
  const { theme: currentTheme } = useTheme();
  const colors = currentTheme === "dark" ? cryoColorsDark : cryoColorsLight;
  const algorithm = currentTheme === "dark" ? theme.darkAlgorithm : theme.defaultAlgorithm;

  return (
    <ConfigProvider
      theme={{
        algorithm,
        token: {
          colorPrimary: colors.primary,
          colorInfo: colors.primary,
          colorBgBase: colors.bg,
          colorBgContainer: colors.bg,
          colorBgElevated: colors.bg,
          colorBgLayout: colors.panel,
          colorText: colors.ink,
          colorTextBase: colors.ink,
          colorTextSecondary: colors.muted,
          colorTextTertiary: colors.mutedSubtle,
          colorBorder: colors.border,
          colorBorderSecondary: colors.border,
          colorFillAlter: colors.tableHeader,
          colorFillSecondary: colors.panel,
          colorFillTertiary: colors.panelStrong,
          borderRadius: 8,
          fontSize: 13,
          fontFamily:
            '"IBM Plex Sans", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
          controlHeight: 32,
          controlHeightSM: 26,
          margin: 12,
          padding: 12
        },
        components: {
          Table: {
            cellPaddingBlock: 7,
            cellPaddingInline: 10,
            headerBg: colors.tableHeader,
            colorBgContainer: colors.bg,
            rowHoverBg: colors.surfaceSunken
          },
          Layout: {
            bodyBg: colors.panel,
            siderBg: "#0c121b",
            headerBg: colors.bg,
            triggerBg: "#0c121b"
          },
          Card: {
            paddingLG: 14,
            colorBgContainer: colors.bg,
            colorTextHeading: colors.ink
          },
          Menu: {
            itemBg: "transparent",
            itemColor: "#c7d2de",
            itemHoverBg: "#141c28",
            itemHoverColor: "#ffffff",
            itemSelectedBg: "rgba(61, 111, 212, 0.15)",
            itemSelectedColor: "#ffffff",
            subMenuItemBg: "#0c121b"
          }
        }
      }}
    >
      <AuthProvider>
        <NotificationProvider>
          <Shell />
        </NotificationProvider>
      </AuthProvider>
    </ConfigProvider>
  );
};

export const App = () => (
  <QueryClientProvider client={queryClient}>
    <ThemeProvider>
      <ThemedApp />
    </ThemeProvider>
  </QueryClientProvider>
);
