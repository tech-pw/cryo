import {
  ApiOutlined,
  BarChartOutlined,
  ControlOutlined,
  DatabaseOutlined,
  HistoryOutlined,
  SettingOutlined
} from "@ant-design/icons";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ConfigProvider, Layout, Menu, Select, Space, Typography, theme } from "antd";
import { useMemo } from "react";
import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AuthProvider, roleOptions, useAuth } from "./lib/auth";
import { cryoColors } from "./lib/theme";
import { Connections } from "./pages/Connections";
import { DatalakeHealth } from "./pages/DatalakeHealth";
import { Presets } from "./pages/Presets";
import { Runs } from "./pages/Runs";
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
  { key: "/tables", icon: <DatabaseOutlined />, label: <Link to="/tables">Tables</Link> },
  { key: "/presets", icon: <ControlOutlined />, label: <Link to="/presets">Presets</Link> },
  { key: "/runs", icon: <HistoryOutlined />, label: <Link to="/runs">Runs</Link> },
  { key: "/connections", icon: <ApiOutlined />, label: <Link to="/connections">Connections</Link> }
];

const Shell = () => {
  const location = useLocation();
  const { token, setToken } = useAuth();
  const selectedKey = useMemo(
    () => navItems.find((item) => location.pathname.startsWith(item.key))?.key ?? "/health",
    [location.pathname]
  );

  return (
    <Layout className="app-shell">
      <Layout.Sider width={212} className="side-nav">
        <div className="brand">
          <SettingOutlined />
          <span>Cryo</span>
        </div>
        <Menu mode="inline" selectedKeys={[selectedKey]} items={navItems} />
      </Layout.Sider>
      <Layout>
        <Layout.Header className="topbar">
          <Typography.Text type="secondary">Apache Iceberg maintenance and health</Typography.Text>
          <Space>
            <Typography.Text type="secondary">Dev role</Typography.Text>
            <Select value={token} options={roleOptions} onChange={setToken} style={{ width: 120 }} />
          </Space>
        </Layout.Header>
        <Layout.Content className="content">
          <Routes>
            <Route path="/" element={<Navigate to="/health" replace />} />
            <Route path="/health" element={<DatalakeHealth />} />
            <Route path="/tables" element={<TableExplorer />} />
            <Route path="/tables/:tableId" element={<TableDetail />} />
            <Route path="/presets" element={<Presets />} />
            <Route path="/runs" element={<Runs />} />
            <Route path="/runs/:runId" element={<Runs />} />
            <Route path="/connections" element={<Connections />} />
          </Routes>
        </Layout.Content>
      </Layout>
    </Layout>
  );
};

export const App = () => (
  <ConfigProvider
    theme={{
      algorithm: theme.defaultAlgorithm,
      token: {
        colorPrimary: cryoColors.primary,
        colorInfo: cryoColors.primary,
        colorBgBase: cryoColors.bg,
        colorBgContainer: cryoColors.bg,
        colorBgElevated: cryoColors.bg,
        colorBgLayout: cryoColors.panel,
        colorText: cryoColors.ink,
        colorTextBase: cryoColors.ink,
        colorTextSecondary: cryoColors.muted,
        colorTextTertiary: cryoColors.mutedSubtle,
        colorBorder: cryoColors.border,
        colorBorderSecondary: cryoColors.border,
        colorFillAlter: cryoColors.tableHeader,
        colorFillSecondary: cryoColors.panel,
        colorFillTertiary: cryoColors.panelStrong,
        borderRadius: 8,
        fontSize: 13,
        fontFamily:
          'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
        controlHeight: 32,
        controlHeightSM: 26,
        margin: 12,
        padding: 12
      },
      components: {
        Table: {
          cellPaddingBlock: 7,
          cellPaddingInline: 10,
          headerBg: cryoColors.tableHeader,
          colorBgContainer: cryoColors.bg,
          rowHoverBg: cryoColors.tableHeader
        },
        Layout: {
          bodyBg: cryoColors.panel,
          siderBg: cryoColors.panelStrong,
          headerBg: cryoColors.bg,
          triggerBg: cryoColors.panelStrong
        },
        Card: {
          paddingLG: 14,
          colorBgContainer: cryoColors.bg,
          colorTextHeading: cryoColors.ink
        },
        Menu: {
          itemBg: "transparent",
          itemColor: cryoColors.ink,
          itemHoverBg: cryoColors.panel,
          itemHoverColor: cryoColors.ink,
          itemSelectedBg: cryoColors.primarySoft,
          itemSelectedColor: cryoColors.primary,
          subMenuItemBg: cryoColors.panelStrong
        }
      }
    }}
  >
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <Shell />
      </AuthProvider>
    </QueryClientProvider>
  </ConfigProvider>
);
