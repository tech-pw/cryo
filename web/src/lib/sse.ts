export interface SseFrame {
  id?: string;
  event: string;
  data: string;
}

export interface SseParser {
  push(chunk: string): SseFrame[];
  finish(): SseFrame[];
}

const normalizeNewlines = (value: string): string => value.replace(/\r\n/g, "\n").replace(/\r/g, "\n");

const parseFrame = (raw: string): SseFrame | null => {
  let id: string | undefined;
  let event = "message";
  const data: string[] = [];
  let sawField = false;

  for (const line of raw.split("\n")) {
    if (!line || line.startsWith(":")) continue;
    sawField = true;
    const separator = line.indexOf(":");
    const field = separator === -1 ? line : line.slice(0, separator);
    let value = separator === -1 ? "" : line.slice(separator + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "id") id = value;
    else if (field === "event") event = value;
    else if (field === "data") data.push(value);
  }

  if (!sawField) return null;
  const frame: SseFrame = { event, data: data.join("\n") };
  if (id !== undefined) frame.id = id;
  return frame;
};

export const createSseParser = (): SseParser => {
  let buffer = "";

  const take = (flush: boolean): SseFrame[] => {
    const frames: SseFrame[] = [];
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const raw = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const frame = parseFrame(raw);
      if (frame) frames.push(frame);
      boundary = buffer.indexOf("\n\n");
    }
    if (flush && buffer.trim()) {
      const frame = parseFrame(buffer);
      buffer = "";
      if (frame) frames.push(frame);
    }
    return frames;
  };

  return {
    push(chunk: string) {
      buffer += normalizeNewlines(chunk);
      return take(false);
    },
    finish() {
      return take(true);
    }
  };
};

export const isAbortError = (error: unknown): boolean =>
  (error instanceof DOMException && error.name === "AbortError") ||
  (error instanceof Error && error.name === "AbortError");

const abortError = (): DOMException => new DOMException("The operation was aborted.", "AbortError");

export const consumeSse = async (
  stream: ReadableStream<Uint8Array>,
  onFrame: (frame: SseFrame) => void,
  signal?: AbortSignal
): Promise<void> => {
  if (signal?.aborted) throw abortError();
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  const parser = createSseParser();

  const cancel = () => {
    void reader.cancel(abortError()).catch(() => undefined);
  };
  signal?.addEventListener("abort", cancel);

  try {
    while (!signal?.aborted) {
      const next = await reader.read();
      if (signal?.aborted) throw abortError();
      if (next.done) break;
      const text = decoder.decode(next.value, { stream: true });
      for (const frame of parser.push(text)) onFrame(frame);
    }
    if (signal?.aborted) throw abortError();
    for (const frame of parser.push(decoder.decode())) onFrame(frame);
    for (const frame of parser.finish()) onFrame(frame);
  } catch (error) {
    if (signal?.aborted || isAbortError(error)) throw abortError();
    throw error;
  } finally {
    signal?.removeEventListener("abort", cancel);
    try {
      reader.releaseLock();
    } catch {
      return;
    }
  }
};
