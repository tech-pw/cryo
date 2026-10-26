import { describe, expect, it } from "vitest";

import { throttledMessage } from "./signInErrors";

const withRetryAfter = (value: string | null) => ({ headers: new Headers(value === null ? {} : { "Retry-After": value }) });

describe("throttledMessage", () => {
  it("rounds Retry-After up to whole minutes", () => {
    expect(throttledMessage(withRetryAfter("61"))).toBe("Too many failed sign-in attempts. Try again in 2 minutes.");
    expect(throttledMessage(withRetryAfter("30"))).toBe("Too many failed sign-in attempts. Try again in 1 minute.");
  });

  it("falls back when the header is missing or invalid", () => {
    expect(throttledMessage(withRetryAfter(null))).toBe("Too many failed sign-in attempts. Try again later.");
    expect(throttledMessage(withRetryAfter("soon"))).toBe("Too many failed sign-in attempts. Try again later.");
  });
});
