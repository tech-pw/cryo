import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { OperationPipeline, maintenanceOpsToSteps, scanPipelineSteps } from "./OperationPipeline";

describe("OperationPipeline", () => {
  it("uses the caller label for a step with no evidence instead of queued", () => {
    render(
      <OperationPipeline
        steps={maintenanceOpsToSteps(["optimize"], null, [], null, { absentLabel: "Not recorded" })}
        title="Maintenance pipeline"
      />
    );
    expect(screen.getByText("Not recorded")).toBeInTheDocument();
    expect(screen.queryByText("queued")).not.toBeInTheDocument();
  });

  it("renders Waiting when the caller says the step is waiting", () => {
    render(
      <OperationPipeline
        steps={[{ key: "optimize", label: "optimize", status: "pending", statusText: "Waiting" }]}
      />
    );
    expect(screen.getByText("Waiting")).toBeInTheDocument();
    expect(screen.queryByText("queued")).not.toBeInTheDocument();
  });

  it("does not relabel a pending step as queued when the caller passes no text", () => {
    render(<OperationPipeline steps={[{ key: "optimize", label: "optimize", status: "pending" }]} />);
    expect(screen.getByText("pending")).toBeInTheDocument();
    expect(screen.queryByText("queued")).not.toBeInTheDocument();
  });

  it("shows partial on a partly succeeded step", () => {
    render(
      <OperationPipeline
        steps={maintenanceOpsToSteps(["optimize"], null, [], null, { partialOps: ["optimize"] })}
      />
    );
    expect(screen.getByText("partial")).toBeInTheDocument();
    expect(screen.queryByText("queued")).not.toBeInTheDocument();
  });

  it("reads an in-progress scan with no step evidence as Waiting", () => {
    render(<OperationPipeline steps={scanPipelineSteps("running")} title="Scan pipeline" />);
    expect(screen.getAllByText("Waiting")).toHaveLength(4);
    expect(screen.queryByText("queued")).not.toBeInTheDocument();
    expect(screen.queryByText("Not recorded")).not.toBeInTheDocument();
  });

  it("reads a finished partial scan as Not recorded and never queued", () => {
    render(<OperationPipeline steps={scanPipelineSteps("partial")} title="Scan pipeline" />);
    expect(screen.getAllByText("Not recorded")).toHaveLength(4);
    expect(screen.queryByText("queued")).not.toBeInTheDocument();
    expect(screen.queryByText("Waiting")).not.toBeInTheDocument();
  });

  it("does not invent queued steps for any finished scan status", () => {
    for (const status of ["succeeded", "failed", "cancelled", "partial_failed"]) {
      const { unmount } = render(<OperationPipeline steps={scanPipelineSteps(status)} title="Scan pipeline" />);
      expect(screen.queryByText("queued")).not.toBeInTheDocument();
      expect(screen.getAllByText("Not recorded")).toHaveLength(4);
      unmount();
    }
  });
});
