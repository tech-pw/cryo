import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { TableRead } from "../api/types";

const mocks = vi.hoisted(() => ({
  previewMaintenance: vi.fn(),
  verifyPlan: vi.fn(),
  narratePlan: vi.fn()
}));
vi.mock("../api/client", () => mocks);
vi.mock("../lib/auth", () => ({ useAuth: () => ({ api: {}, token: "t" }) }));

import { PlanPreview } from "./PlanPreview";

const table = (name: string) => ({ id: name, schema_name: "db", table_name: name, stats: {} }) as unknown as TableRead;

const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
const wrap = (node: ReactNode) => (
  <QueryClientProvider client={client}>
    <MemoryRouter>{node}</MemoryRouter>
  </QueryClientProvider>
);

describe("PlanPreview refetch", () => {
  beforeEach(() => {
    mocks.previewMaintenance.mockReset().mockResolvedValue({ items: [] });
  });

  it("refetches when the table changes even if the count stays the same", async () => {
    const { rerender } = render(wrap(<PlanPreview open tables={[table("a")]} onClose={() => undefined} />));
    await waitFor(() => expect(mocks.previewMaintenance).toHaveBeenCalledTimes(1));
    expect(mocks.previewMaintenance.mock.calls[0][1].tables[0].table_name).toBe("a");

    rerender(wrap(<PlanPreview open tables={[table("b")]} onClose={() => undefined} />));
    await waitFor(() => expect(mocks.previewMaintenance).toHaveBeenCalledTimes(2));
    expect(mocks.previewMaintenance.mock.calls[1][1].tables[0].table_name).toBe("b");

    // A new array holding the same table does not refetch.
    rerender(wrap(<PlanPreview open tables={[table("b")]} onClose={() => undefined} />));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(mocks.previewMaintenance).toHaveBeenCalledTimes(2);
  });
});
