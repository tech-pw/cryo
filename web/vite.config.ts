import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const apiProxyTarget = process.env.VITE_PROXY_TARGET || "http://localhost:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    rollupOptions: {
      output: {
        // Vite 8 bundles with Rolldown, which only takes the function form.
        manualChunks(id: string) {
          if (!id.includes("node_modules")) return undefined;
          if (/[\\/]node_modules[\\/](recharts|d3-[^\\/]+|victory-vendor)[\\/]/.test(id)) {
            return "recharts";
          }
          if (
            /[\\/]node_modules[\\/](react|react-dom|react-router|react-router-dom|scheduler|@tanstack[\\/]react-query|@tanstack[\\/]query-core)[\\/]/.test(
              id
            )
          ) {
            return "react-vendor";
          }
          return undefined;
        }
      }
    }
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: apiProxyTarget,
        changeOrigin: true,
        ws: true
      },
      "/healthz": {
        target: apiProxyTarget,
        changeOrigin: true
      },
      "/metrics": {
        target: apiProxyTarget,
        changeOrigin: true
      }
    }
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    exclude: ["**/node_modules/**", "**/e2e/**"]
  }
});
