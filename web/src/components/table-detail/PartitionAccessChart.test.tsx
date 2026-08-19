import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PartitionAccessChart } from "./PartitionAccessChart";

describe("PartitionAccessChart", () => {
  it("shows an empty-state message when there are no points", () => {
    render(<PartitionAccessChart points={[]} partitions={[]} />);
    expect(screen.getByText("No partition access data yet.")).toBeInTheDocument();
  });

  it("renders the accessible data-table companion with partition rows", () => {
    render(
      <PartitionAccessChart
        points={[
          { day: "2026-08-19", partition_path: "dt=2026-08-19/", request_count: 42 },
          { day: "2026-08-18", partition_path: "dt=2026-08-18/", request_count: 7 }
        ]}
        partitions={[
          {
            partition_path: "dt=2026-08-19/",
            total_requests: 42,
            first_seen: "2026-08-19",
            last_seen: "2026-08-19",
            days_since_last_access: 0
          },
          {
            partition_path: "dt=2026-08-18/",
            total_requests: 7,
            first_seen: "2026-08-18",
            last_seen: "2026-08-18",
            days_since_last_access: 1
          }
        ]}
      />
    );

    // ChartDataTable renders behind a <details> disclosure -- its content is present in the
    // DOM (not conditionally mounted on open), so a plain text query finds it directly.
    expect(screen.getByText("dt=2026-08-19/")).toBeInTheDocument();
    expect(screen.getByText("dt=2026-08-18/")).toBeInTheDocument();
    expect(screen.getByText("Today")).toBeInTheDocument();
    expect(screen.getByText("1d ago")).toBeInTheDocument();
  });

  it("labels an empty partition_path as unpartitioned in the data table", () => {
    render(
      <PartitionAccessChart
        points={[{ day: "2026-08-19", partition_path: "", request_count: 5 }]}
        partitions={[
          {
            partition_path: "",
            total_requests: 5,
            first_seen: "2026-08-19",
            last_seen: "2026-08-19",
            days_since_last_access: 0
          }
        ]}
      />
    );

    expect(screen.getByText("(unpartitioned)")).toBeInTheDocument();
  });
});
