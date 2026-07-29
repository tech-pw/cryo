/**
 * Combines a caller-provided AbortSignal (used for request cancellation on debounce/unmount)
 * with a timeout, so a request that never settles -- a hung backend, a flaky proxy, a dropped
 * connection between the browser and the dev server -- always eventually aborts into an error
 * state instead of leaving the UI (e.g. a "Searching..." spinner) stuck forever with no
 * recourse.
 */
export const withTimeoutSignal = (signal: AbortSignal | undefined, timeoutMs: number): AbortSignal => {
  const controller = new AbortController();

  if (signal?.aborted) {
    controller.abort();
    return controller.signal;
  }

  const onAbort = () => controller.abort();
  signal?.addEventListener("abort", onAbort, { once: true });

  const timer = window.setTimeout(() => controller.abort(), timeoutMs);

  controller.signal.addEventListener(
    "abort",
    () => {
      window.clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
    },
    { once: true }
  );

  return controller.signal;
};
