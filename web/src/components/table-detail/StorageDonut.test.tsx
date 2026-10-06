import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StorageDonut } from "./StorageDonut";

describe("StorageDonut", () => {
  it("names each byte measure and does not treat a null part as 0 GB", () => {
    render(
      <StorageDonut
        liveBytes={null}
        deadBytes={0}
        metadataBytes={1024 ** 3}
        orphanBytes={null}
        pricePerGbMonth={0.023}
      />
    );

    expect(screen.getByText("2 of 4 measured")).toBeInTheDocument();
    expect(screen.getByText("Data").closest("div")).toHaveTextContent("Not measured");
    expect(screen.getByText("Data").closest("div")).not.toHaveTextContent("0 GB");
    expect(screen.getByText("Orphan").closest("div")).toHaveTextContent("Not measured");
    expect(screen.getByText("Delete").closest("div")).toHaveTextContent("0 GB");
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });
});
