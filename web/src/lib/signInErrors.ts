/** Message for a 429 from the sign-in endpoints, which send Retry-After in seconds (#262). */
export function throttledMessage(response: Pick<Response, "headers">): string {
  const seconds = Number(response.headers.get("Retry-After"));
  if (!Number.isFinite(seconds) || seconds <= 0) {
    return "Too many failed sign-in attempts. Try again later.";
  }
  const minutes = Math.ceil(seconds / 60);
  return `Too many failed sign-in attempts. Try again in ${minutes} minute${minutes === 1 ? "" : "s"}.`;
}
