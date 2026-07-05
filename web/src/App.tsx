import {
  ApiOutlined,
  BarChartOutlined,
  ClockCircleOutlined,
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
  ThunderboltOutlined
} from "@ant-design/icons";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Button, ConfigProvider, Layout, Menu, Select, Space, Tooltip, Typography, theme } from "antd";
import type { MenuProps } from "antd";
import { useMemo } from "react";
import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AuthProvider, roleOptions, useAuth } from "./lib/auth";
import { cryoColorsLight, cryoColorsDark } from "./lib/theme";
import { ThemeProvider, useTheme } from "./lib/themeContext";
import { Connections } from "./pages/Connections";
import { CostAttribution } from "./pages/CostAttribution";
import { CostSavings } from "./pages/CostSavings";
import { DatalakeHealth } from "./pages/DatalakeHealth";
import { Intelligence } from "./pages/Intelligence";
import { JobDetail } from "./pages/JobDetail";
import LayoutOptimization from "./pages/LayoutOptimization";
import { MultiCatalog } from "./pages/MultiCatalog";
import { Presets } from "./pages/Presets";
import { QueryPerformance } from "./pages/QueryPerformance";
import { Runs } from "./pages/Runs";
import { SLAStatus } from "./pages/SLAStatus";
import { Scans } from "./pages/Scans";
import { TableDetail } from "./pages/TableDetail";
import { TableExplorer } from "./pages/TableExplorer";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      retry: 1
    }
  }
});

const navItems = [
  { key: "/health", icon: <BarChartOutlined />, label: <Link to="/health">Health</Link> },
  { key: "/catalogs", icon: <DatabaseOutlined />, label: <Link to="/catalogs">Catalogs</Link> },
  { key: "/tables", icon: <DatabaseOutlined />, label: <Link to="/tables">Tables</Link> },
  { key: "/scans", icon: <SyncOutlined />, label: <Link to="/scans">Scanner</Link> },
  { key: "/ai", icon: <RobotOutlined />, label: <Link to="/ai">Intelligence</Link> },
  { key: "/queries", icon: <ThunderboltOutlined />, label: <Link to="/queries">Queries</Link> },
  { key: "/cost", icon: <DollarOutlined />, label: <Link to="/cost">Savings</Link> },
  { key: "/attribution", icon: <TeamOutlined />, label: <Link to="/attribution">Attribution</Link> },
  { key: "/sla", icon: <ClockCircleOutlined />, label: <Link to="/sla">SLA</Link> },
  { key: "/optimize", icon: <ExperimentOutlined />, label: <Link to="/optimize">Optimize</Link> },
  { key: "/presets", icon: <ControlOutlined />, label: <Link to="/presets">Presets</Link> },
  { key: "/runs", icon: <HistoryOutlined />, label: <Link to="/runs">Runs</Link> },
  { key: "/connections", icon: <ApiOutlined />, label: <Link to="/connections">Connections</Link> }
];

const navGroups: MenuProps["items"] = [
  {
    type: "group",
    label: "Overview",
    children: [navItems[0], navItems[8]]
  },
  {
    type: "group",
    label: "Inventory",
    children: [navItems[1], navItems[2], navItems[3]]
  },
  {
    type: "group",
    label: "Operations",
    children: [navItems[9], navItems[10], navItems[11]]
  },
  {
    type: "group",
    label: "Insights",
    children: [navItems[4], navItems[5], navItems[6], navItems[7]]
  },
  {
    type: "group",
    label: "Settings",
    children: [navItems[12]]
  }
];

const routeSelectionAliases = new Map([["/jobs", "/runs"]]);

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
  const { token, setToken } = useAuth();
  const selectedKey = useMemo(() => {
    const alias = [...routeSelectionAliases.entries()].find(([route]) => location.pathname.startsWith(route))?.[1];
    return alias ?? navItems.find((item) => location.pathname.startsWith(item.key))?.key ?? "/health";
  }, [location.pathname]);

  return (
    <Layout className="app-shell">
      <Layout.Sider width={212} className="side-nav">
        <div className="brand">
          <SettingOutlined />
          <span>Cryo</span>
        </div>
        <Menu mode="inline" selectedKeys={[selectedKey]} items={navGroups} />
      </Layout.Sider>
      <Layout>
        <Layout.Header className="topbar">
          <Typography.Text type="secondary">Apache Iceberg maintenance and health</Typography.Text>
          <Space>
            <ThemeToggle />
            <Typography.Text type="secondary">Dev role</Typography.Text>
            <Select value={token} options={roleOptions} onChange={setToken} style={{ width: 120 }} />
          </Space>
        </Layout.Header>
        <Layout.Content className="content">
          <Routes>
            <Route path="/" element={<Navigate to="/health" replace />} />
            <Route path="/health" element={<DatalakeHealth />} />
            <Route path="/catalogs" element={<MultiCatalog />} />
            <Route path="/cost" element={<CostSavings />} />
            <Route path="/attribution" element={<CostAttribution />} />
            <Route path="/tables" element={<TableExplorer />} />
            <Route path="/tables/:tableId" element={<TableDetail />} />
            <Route path="/scans" element={<Scans />} />
            <Route path="/scans/:runId" element={<Scans />} />
            <Route path="/ai" element={<Intelligence />} />
            <Route path="/queries" element={<QueryPerformance />} />
            <Route path="/sla" element={<SLAStatus />} />
            <Route path="/optimize" element={<LayoutOptimization />} />
            <Route path="/presets" element={<Presets />} />
            <Route path="/runs" element={<Runs />} />
            <Route path="/runs/:runId" element={<Runs />} />
            <Route path="/jobs/:jobId" element={<JobDetail />} />
            <Route path="/connections" element={<Connections />} />
          </Routes>
        </Layout.Content>
      </Layout>
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
        <Shell />
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
