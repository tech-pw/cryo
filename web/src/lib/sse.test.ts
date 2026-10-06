import { describe, expect, it } from "vitest";
import { consumeSse, createSseParser } from "./sse";

describe("sse parser", () => {
  it("handles split chunks", () => {
    const parser = createSseParser();
    expect(parser.push("id: 7\neve")).toEqual([]);
    expect(parser.push('nt: step.completed\ndata: {"step":2}\n\n')).toEqual([
      { id: "7", event: "step.completed", data: '{"step":2}' }
    ]);
  });

  it("parses event types", () => {
    const parser = createSseParser();
    const frames = parser.push(
      'event: answer.block\ndata: {"type":"answer"}\n\nevent: heartbeat\ndata: {}\n\n'
    );
    expect(frames.map((frame) => frame.event)).toEqual(["answer.block", "heartbeat"]);
    expect(frames[0]?.data).toBe('{"type":"answer"}');
  });

  it("aborts an open stream", async () => {
    const controller = new AbortController();
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      pull(source) {
        source.enqueue(encoder.encode("event: heartbeat\ndata: {}\n\n"));
      }
    });
    const pending = consumeSse(
      stream,
      () => {
        controller.abort();
      },
      controller.signal
    );
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });
});
