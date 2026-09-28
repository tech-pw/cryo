import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StorageDonut } from "./StorageDonut";

describe("StorageDonut", () => {
  it("does not treat an unmeasured orphan slice as 0 GB", () => {
    const { rerender } = render(
      <StorageDonut liveBytes={0} deadBytes={0} metadataBytes={0} orphanBytes={null} pricePerGbMonth={0.023} />
    );
    expect(screen.getByText("Not measured")).toBeInTheDocument();
    expect(screen.queryByText("0 GB")).not.toBeInTheDocument();

    rerender(
      <StorageDonut
        liveBytes={1024 ** 3}
        deadBytes={0}
        metadataBytes={0}
        orphanBytes={null}
        pricePerGbMonth={0.023}
      />
    );
    expect(screen.getByText(/3 of 4 measured/)).toBeInTheDocument();
    expect(screen.getByText("Not measured")).toBeInTheDocument();
    expect(screen.queryByText(/^0 GB$/)).not.toBeInTheDocument();
  });
});
