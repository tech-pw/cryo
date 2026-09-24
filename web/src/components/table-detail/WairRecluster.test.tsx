import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { WairBoundaryReport } from "../../api/types";
import { WairReport } from "./WairRecluster";

const base: WairBoundaryReport = {
  table_id: "00000000-0000-0000-0000-000000000001",
  status: "abstained",
  boundary_file_count: 0,
  selected_file_count: 0,
  proposed_rewrite_gb: 0,
  selected_file_paths: [],
  abstention_reason: "no_boundary_files",
  window_days: 7,
  clustering_column: null,
  savings_cpu_seconds_per_window: 0,
  recluster_cpu_seconds: 0,
  net_cpu_seconds: 0,
  aggressiveness: 1,
  rewrite_gb_per_hour: null,
  boundary_values: [],
  spark_sql: null,
  model_notes: []
};

describe("WairReport", () => {
  it("explains an abstention in plain language", () => {
    render(<WairReport report={base} />);
    expect(screen.getByTestId("wair-abstained")).toHaveTextContent(
      "The layout already fits the workload."
    );
  });

  it("shows the net benefit and the recommended Spark statement", () => {
    render(
      <WairReport
        report={{
          ...base,
          status: "analyzed",
          abstention_reason: null,
          boundary_file_count: 12,
          selected_file_count: 8,
          proposed_rewrite_gb: 3.25,
          clustering_column: "event_date",
          savings_cpu_seconds_per_window: 36_000,
          recluster_cpu_seconds: 7_200,
          net_cpu_seconds: 28_800,
          boundary_values: ["2026-09-01", "2026-09-08"],
          spark_sql: "CALL ice.system.rewrite_data_files(table => 'ice.a.b')",
          model_notes: ["Single global clustering key."]
        }}
      />
    );
    expect(screen.getByText("10 CPU-h")).toBeInTheDocument();
    expect(screen.getByText("8.0 CPU-h")).toBeInTheDocument();
    expect(screen.getByText("8 of 12 (3.3 GB)")).toBeInTheDocument();
    expect(screen.getByText("CALL ice.system.rewrite_data_files(table => 'ice.a.b')")).toBeInTheDocument();
  });
});
