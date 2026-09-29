import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LoadingSkeleton, skeletonStalled } from "./skeleton";

describe("LoadingSkeleton", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("stops the shimmer after five seconds", async () => {
    expect(skeletonStalled(4999)).toBe(false);
    expect(skeletonStalled(5000)).toBe(true);
    render(<LoadingSkeleton timeoutMs={0} />);
    expect(await screen.findByText("Still loading…")).toBeInTheDocument();
  });
});
