import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { REDUCED_MOTION_QUERY } from "../../lib/motion";
import { MAINTENANCE_OPERATION_ORDER } from "../../lib/operationOrder";
import { createEmptyDraft, type BatchIdentity } from "../../lib/maintenanceBatch";
import { AddToBatchButton } from "./AddToBatchButton";
import { BatchPanel } from "./BatchPanel";

const identity: BatchIdentity = {
  installationId: "install-1",
  principalId: "user-1",
  tenantId: "tenant-1"
};

const draft = {
  ...createEmptyDraft(identity),
  tables: [
    { id: "1", label: "catalog › schema › one" },
    { id: "2", label: "catalog › schema › two" }
  ]
};

function stubReducedMotion(matches: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query === REDUCED_MOTION_QUERY ? matches : false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false
  }));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("BatchPanel", () => {
  it("names the run count and shows recipe chips in canonical order", () => {
    render(
      <BatchPanel
        identity={identity}
        draft={draft}
        readyCount={11}
        blockedCount={2}
        nothingToDoCount={1}
      />
    );

    expect(screen.getByRole("region", { name: "Maintenance batch" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Maintenance batch" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Compact" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Compact and clean up" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Metadata only" })).toBeInTheDocument();
    expect(screen.getByText("Ready").previousElementSibling).toHaveTextContent("11");
    expect(screen.getByText("Blocked").previousElementSibling).toHaveTextContent("2");
    expect(screen.getByText("Nothing to do").previousElementSibling).toHaveTextContent("1");

    const operations = screen.getAllByTestId("batch-operation").map((node) => node.textContent);
    const indexes = operations.map((operation) =>
      MAINTENANCE_OPERATION_ORDER.indexOf(operation as (typeof MAINTENANCE_OPERATION_ORDER)[number])
    );
    expect(indexes).toEqual([...indexes].sort((left, right) => left - right));
    expect(operations).toEqual([
      "optimize",
      "optimize_manifests",
      "expire_snapshots",
      "remove_orphan_files"
    ]);

    const run = screen.getByRole("button", { name: "Run maintenance on 11 tables" });
    expect(run).toBeEnabled();
    expect(screen.getByLabelText("Resources")).toHaveTextContent("11 separate Spark jobs");
    expect(screen.getByLabelText("Resources")).not.toHaveTextContent(/lane/i);
  });

  it("disables the run button when the count is 0", () => {
    render(<BatchPanel identity={identity} draft={createEmptyDraft(identity)} readyCount={0} />);
    expect(screen.getByRole("button", { name: "Run maintenance on 0 tables" })).toBeDisabled();
  });

  it("says Not estimated instead of 0", () => {
    render(
      <BatchPanel
        identity={identity}
        draft={draft}
        readyCount={4}
        blockedCount={1}
        nothingToDoCount={1}
        rewriteEstimate={0}
        reclaimEstimate={null}
      />
    );
    const expected = screen.getByLabelText("Expected");
    expect(expected).toHaveTextContent("Not estimated");
    expect(expected).not.toHaveTextContent("0");
    expect(expected).toHaveTextContent("Rewrite: Not estimated · Reclaim: Not estimated");
  });

  it("does not say queue or cart", () => {
    const { container } = render(
      <>
        <BatchPanel
          identity={identity}
          draft={draft}
          readyCount={11}
          blockedCount={2}
          nothingToDoCount={1}
          destinationLabel="spark-prod"
          presetLabel="nightly-default"
        />
        <AddToBatchButton identity={identity} tables={[{ id: "9", label: "catalog › schema › nine" }]} />
        <AddToBatchButton
          identity={identity}
          tables={[
            { id: "9", label: "catalog › schema › nine" },
            { id: "8", label: "catalog › schema › eight" }
          ]}
        />
        <AddToBatchButton
          identity={identity}
          matching={{ filter: { all: [] }, declaredCount: 312, pageIds: ["page-only"] }}
        />
      </>
    );
    expect(container.textContent?.toLowerCase()).not.toMatch(/queue|cart/);
    expect(screen.getByRole("button", { name: "Add to batch" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add selected to batch" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add all 312 matching to batch" })).toBeInTheDocument();
  });

  it("omits the highlight animation when reduced motion is preferred", () => {
    stubReducedMotion(true);
    render(<BatchPanel identity={identity} draft={draft} readyCount={2} pulse />);
    expect(screen.getByRole("region", { name: "Maintenance batch" }).className).not.toContain("motion-highlight");
  });

  it("applies the highlight animation when motion is allowed", () => {
    stubReducedMotion(false);
    render(<BatchPanel identity={identity} draft={draft} readyCount={2} pulse />);
    expect(screen.getByRole("region", { name: "Maintenance batch" }).className).toContain("motion-highlight");
  });

  it("switches recipe chips without leaving canonical order", () => {
    render(<BatchPanel identity={identity} draft={draft} readyCount={2} />);
    fireEvent.click(screen.getByRole("button", { name: "Metadata only" }));
    expect(screen.getAllByTestId("batch-operation").map((node) => node.textContent)).toEqual([
      "optimize_manifests"
    ]);
    fireEvent.click(screen.getByRole("button", { name: "Compact" }));
    expect(screen.getAllByTestId("batch-operation").map((node) => node.textContent)).toEqual(["optimize"]);
  });
});
