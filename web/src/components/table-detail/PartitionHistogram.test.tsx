import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PartitionHistogram } from "./PartitionHistogram";

describe("PartitionHistogram", () => {
  it("ranks the top partitions by size and keeps full names", () => {
    const partitions = [
      { partition_value: "2024-01-01", partition_size_gb: 2, files_per_partition: 10, small_files_in_partition_count: 3 },
      {
        partition_value: "a-very-long-partition-name-that-must-stay-visible",
        partition_size_gb: 10,
        files_per_partition: 4,
        small_files_in_partition_count: 1
      },
      ...Array.from({ length: 14 }, (_, index) => ({
        partition_value: `p-${index}`,
        partition_size_gb: 1,
        files_per_partition: 2,
        small_files_in_partition_count: 0
      }))
    ];

    render(<PartitionHistogram partitions={partitions} />);

    expect(screen.getByText("Top 15 of 16 partitions")).toBeInTheDocument();
    expect(screen.getByText("a-very-long-partition-name-that-must-stay-visible")).toBeInTheDocument();
    expect(screen.getByText("10 GB")).toBeInTheDocument();
    expect(screen.getByText("25%")).toBeInTheDocument();
    expect(screen.queryByText("p-13")).not.toBeInTheDocument();
    const names = screen.getAllByRole("row").slice(1).map((row) => row.textContent ?? "");
    expect(names[0]).toContain("a-very-long-partition-name-that-must-stay-visible");
  });

  it("does not treat a missing size or small-file rate as 0", () => {
    render(
      <PartitionHistogram
        partitions={[
          { partition_value: "known", partition_size_gb: 4, files_per_partition: 8, small_files_in_partition_count: 2 },
          { partition_value: null, partition_size_gb: undefined, files_per_partition: undefined, small_files_in_partition_count: undefined }
        ]}
      />
    );

    expect(screen.getByText("Top 2 of 2 partitions")).toBeInTheDocument();
    expect(screen.getAllByText("Not measured").length).toBeGreaterThan(0);
    expect(screen.queryByText("0 GB")).not.toBeInTheDocument();
    expect(screen.queryByText("0%")).not.toBeInTheDocument();
  });
});
