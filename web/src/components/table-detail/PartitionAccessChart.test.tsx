import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PartitionAccessChart } from "./PartitionAccessChart";

describe("PartitionAccessChart", () => {
  it("shows an empty-state message when there are no points", () => {
    render(<PartitionAccessChart points={[]} partitions={[]} />);
    expect(screen.getByText("No partition access data yet.")).toBeInTheDocument();
  });

  it("ranks partitions by requests and summarises concentration", () => {
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

    expect(screen.getByText("partitions read", { exact: false })).toBeInTheDocument();
    const ranked = [...document.querySelectorAll(".td-pa-top .td-pa-name")].map((node) => node.textContent);
    expect(ranked).toEqual(["dt=2026-08-19", "dt=2026-08-18"]);
    expect(screen.getAllByText("Today").length).toBeGreaterThan(0);
    expect(screen.getAllByText("1d ago").length).toBeGreaterThan(0);
    expect(screen.getAllByText("42").length).toBeGreaterThan(0);
  });

  it("does not plot a null request count as zero", () => {
    render(
      <PartitionAccessChart
        points={[{ day: "2026-08-19", partition_path: "dt=a/", request_count: null }]}
        partitions={[
          {
            partition_path: "dt=a/",
            total_requests: null,
            first_seen: "2026-08-19",
            last_seen: "2026-08-19",
            days_since_last_access: null
          }
        ]}
      />
    );

    expect(screen.getAllByText("Not measured").length).toBeGreaterThan(0);
    expect(screen.queryByText(/^0$/)).not.toBeInTheDocument();
    expect(screen.queryByText("Today")).not.toBeInTheDocument();
  });

  it("decodes double-encoded S3 partition keys", () => {
    render(
      <PartitionAccessChart
        points={[]}
        partitions={[
          {
            partition_path: "ingest_date%253D2026-09-16/",
            total_requests: 3,
            first_seen: "2026-09-16",
            last_seen: "2026-09-16",
            days_since_last_access: 0
          }
        ]}
      />
    );
    expect(screen.getAllByText("ingest_date=2026-09-16").length).toBeGreaterThan(0);
    expect(screen.queryByText(/%25/)).not.toBeInTheDocument();
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

    expect(screen.getAllByText("(unpartitioned)").length).toBeGreaterThan(0);
  });
});
