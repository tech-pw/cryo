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

  it("keeps the scan pipeline pending label supplied by its caller", () => {
    render(<OperationPipeline steps={scanPipelineSteps("accepted")} title="Scan pipeline" />);
    expect(screen.getAllByText("queued").length).toBeGreaterThan(0);
  });
});
