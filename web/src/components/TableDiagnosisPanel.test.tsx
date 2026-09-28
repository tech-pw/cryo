import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { buildDiagnosisVerdict, TableDiagnosisPanel } from "./TableDiagnosisPanel";

vi.mock("../lib/auth", () => ({
  useAuth: () => ({ api: { token: "test-token" }, token: "test-token" })
}));

vi.mock("../api/client", () => ({
  getTableSignals: vi.fn().mockResolvedValue({
    signals: [],
    checked_at: "2026-09-28T00:00:00Z"
  })
}));

describe("TableDiagnosisPanel", () => {
  it("does not call an unscanned table healthy", async () => {
    expect(buildDiagnosisVerdict(null)).toEqual({ level: "warning", message: "Not scanned yet" });
    expect(buildDiagnosisVerdict({ grade: "-" })).toEqual({ level: "warning", message: "Not scanned yet" });
    expect(buildDiagnosisVerdict(null).message).not.toMatch(/healthy/i);

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <TableDiagnosisPanel tableId="table-1" stats={null} factors={[]} />
      </QueryClientProvider>
    );

    expect(await screen.findByText("Not scanned yet")).toBeInTheDocument();
    expect(screen.queryByText(/healthy, no action needed/i)).not.toBeInTheDocument();
  });
});
