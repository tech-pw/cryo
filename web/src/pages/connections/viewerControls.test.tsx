import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { VIEWER_REASON } from "../../lib/permissions";
import { LakehousesTab } from "./LakehousesTab";

vi.mock("../../lib/auth", () => ({
  useAuth: () => ({
    api: { token: "test-token" },
    token: "test-token",
    role: "viewer",
    subject: "viewer"
  })
}));

vi.mock("../../api/client", () => ({
  patchSource: vi.fn()
}));

vi.mock("./ConnectionsContext", () => ({
  useConnectionsContext: () => ({
    api: { token: "test-token" },
    sources: {
      data: [{ id: "src-1", name: "prod", type: "rest", enabled: true, config: {} }],
      isLoading: false
    },
    awsStatuses: {},
    pendingScanVersion: 0,
    setSourceDrawer: vi.fn(),
    setLatencySource: vi.fn(),
    purgeSourceMutation: { isPending: false, mutate: vi.fn() },
    queryClient: { invalidateQueries: vi.fn() }
  })
}));

describe("Connections viewer controls", () => {
  it("disables operator writes and hides admin purge", () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <LakehousesTab />
      </QueryClientProvider>
    );

    expect(screen.getByText(VIEWER_REASON)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /New lakehouse/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Edit" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Disable" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Latency" })).toBeEnabled();
  });
});
