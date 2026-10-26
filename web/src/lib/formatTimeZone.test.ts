import { afterEach, describe, expect, it } from "vitest";

import {
  formatClock,
  formatDate,
  formatDateTime,
  formatShortDate,
  formatTime,
  getDisplayTimeZone,
  setDisplayTimeZone,
  zoneLabel
} from "./format";

/** 2026-10-20T12:00:00Z -- a fixed instant so the assertions do not depend on "now". */
const INSTANT = "2026-10-20T12:00:00Z";

describe("display time zone (#183)", () => {
  afterEach(() => {
    setDisplayTimeZone(undefined);
  });

  it("defaults to no explicit zone, so the browser's zone is used", () => {
    expect(getDisplayTimeZone()).toBeUndefined();
  });

  it("renders instants in the install's zone, not the browser's", () => {
    setDisplayTimeZone("Asia/Kolkata");
    // 12:00 UTC is 17:30 in Kolkata (UTC+05:30). The clock format follows the locale,
    // so assert on the hour and minute separately rather than on "17:30" literally.
    expect(formatTime(INSTANT)).toMatch(/(17:30|05:30\s?PM)/);
  });

  it("renders instants in UTC when the install is on UTC", () => {
    setDisplayTimeZone("UTC");
    expect(formatTime(INSTANT)).toMatch(/(12:00|12:00\s?PM)/);
  });

  it("keeps the browser zone until the install's zone arrives", () => {
    setDisplayTimeZone(undefined);
    const browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    expect(formatTime(INSTANT)).toBe(
      new Intl.DateTimeFormat(undefined, {
        month: "short",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        timeZone: browserZone
      }).format(new Date(INSTANT))
    );
  });

  it("ignores an empty zone", () => {
    setDisplayTimeZone("   ");
    expect(getDisplayTimeZone()).toBeUndefined();
  });

  it("labels the zone so a reader knows which clock they are looking at", () => {
    setDisplayTimeZone("Asia/Kolkata");
    expect(zoneLabel()).toBe("Kolkata");
    setDisplayTimeZone("UTC");
    expect(zoneLabel()).toBe("UTC");
    setDisplayTimeZone(undefined);
    expect(zoneLabel()).toBe("local");
  });

  it("formats a short date in the install's zone", () => {
    setDisplayTimeZone("UTC");
    // 18:30 UTC is still the 20th in UTC, but already the 21st in Kolkata.
    expect(formatShortDate("2026-10-20T18:30:00Z")).toMatch(/20.*Oct|Oct.*20/);
    setDisplayTimeZone("Asia/Kolkata");
    expect(formatShortDate("2026-10-20T18:30:00Z")).toMatch(/21.*Oct|Oct.*21/);
  });

  it("formats a date with the year in the install's zone", () => {
    setDisplayTimeZone("UTC");
    expect(formatDate("2026-10-20T18:30:00Z")).toContain("2026");
  });

  it("formats a date and time in the install's zone", () => {
    setDisplayTimeZone("UTC");
    expect(formatDateTime(INSTANT)).toContain("12:00");
  });

  it("formats the clock in the install's zone", () => {
    setDisplayTimeZone("Asia/Kolkata");
    expect(formatClock(INSTANT)).toBe("17:30");
  });

  it("returns a dash for a missing or invalid instant", () => {
    expect(formatShortDate("not a date")).toBe("-");
    expect(formatDateTime("not a date")).toBe("-");
    expect(formatClock("not a date")).toBe("-");
  });
});
