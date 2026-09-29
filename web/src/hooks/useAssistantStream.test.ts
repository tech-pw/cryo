import { describe, expect, it } from "vitest";
import { frameToAssistantEvent } from "../api/assistant";
import { initialAssistantStreamState, reduceAssistantEvent } from "./useAssistantStream";

describe("assistant stream reducer", () => {
  it("applies step.completed and answer.block", () => {
    const started = reduceAssistantEvent(initialAssistantStreamState, {
      event: "step.started",
      data: { step: 1, tool_name: "list_tables", label: "List tables" }
    });
    const completed = reduceAssistantEvent(started, {
      event: "step.completed",
      data: { step: 1, duration_ms: 40, row_count: 2, evidence_id: "ev-1" }
    });
    expect(completed.steps).toEqual([
      expect.objectContaining({
        step: 1,
        status: "completed",
        toolName: "list_tables",
        label: "List tables",
        durationMs: 40,
        rowCount: 2,
        evidenceId: "ev-1"
      })
    ]);

    const block = { type: "answer" as const, text: "Compaction is behind." };
    const withBlock = reduceAssistantEvent(completed, { event: "answer.block", data: { block } });
    expect(withBlock.blocks).toEqual([block]);
  });

  it("unwraps the SSE envelope before applying step.completed", () => {
    const event = frameToAssistantEvent({
      id: "4",
      event: "step.completed",
      data: JSON.stringify({
        v: 1,
        message_id: "msg-1",
        seq: 4,
        type: "step.completed",
        payload: { step: 2, duration_ms: 15, row_count: 3, evidence_id: "ev-9", tool: "list_tables" }
      })
    });
    expect(event?.event).toBe("step.completed");
    if (event?.event !== "step.completed") return;
    const next = reduceAssistantEvent(initialAssistantStreamState, event);
    expect(next.steps[0]).toMatchObject({
      step: 2,
      status: "completed",
      durationMs: 15,
      rowCount: 3,
      evidenceId: "ev-9",
      toolName: "list_tables"
    });
  });
});
