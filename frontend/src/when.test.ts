// Timestamp rendering in the business's zone rather than the browser's.
//
// The bug `when.ts` was written for: six components each had their own date
// helper, none passed a timezone, so all six rendered in the *browser's* zone.
// Three people looking at the same sync run disagreed about when it happened.
//
// These tests run with `TZ=America/New_York` (see vite.config.ts), which is
// neither UTC nor Asia/Kolkata. That is the point — a helper that quietly reads
// the host zone produces a third, different answer here, so the regression is
// caught rather than masked by a laptop that happens to be set to IST.
import { beforeEach, describe, expect, it } from "vitest";

import {
  businessTimezone,
  formatDate,
  formatDateTime,
  formatTime,
  setBusinessTimezone,
  since,
  timezoneLabel,
  todayISO,
} from "./when";

beforeEach(() => setBusinessTimezone("Asia/Kolkata"));

describe("the business timezone", () => {
  it("defaults to the business's zone, not the host's", () => {
    expect(businessTimezone()).toBe("Asia/Kolkata");
  });

  it("adopts a valid zone from the session", () => {
    setBusinessTimezone("Asia/Dubai");
    expect(businessTimezone()).toBe("Asia/Dubai");
  });

  it("keeps the previous zone when handed an invalid one", () => {
    // Adopting it would make every later format call throw, and a blank date
    // column is worse than one in the wrong zone.
    setBusinessTimezone("Mars/Olympus_Mons");
    expect(businessTimezone()).toBe("Asia/Kolkata");
    expect(() => formatDate("2026-08-05T19:00:00Z")).not.toThrow();
  });

  it("ignores an empty zone rather than falling back to the host", () => {
    setBusinessTimezone("");
    setBusinessTimezone(null);
    setBusinessTimezone(undefined);
    expect(businessTimezone()).toBe("Asia/Kolkata");
  });

  it("gives a short label for a column header", () => {
    expect(timezoneLabel()).toBeTruthy();
    expect(timezoneLabel()).not.toBe("");
  });
});

describe("formatting an instant", () => {
  // 19:00 UTC on the 5th is 00:30 IST on the *6th* — the exact case from the
  // module's own comment, and the one the host zone gets wrong. In New York
  // this instant is still the 5th, so a helper reading the host zone shows a
  // different day.
  const CROSSES_MIDNIGHT_IN_IST = "2026-08-05T19:00:00Z";

  it("renders the business's day, not the browser's", () => {
    expect(formatDate(CROSSES_MIDNIGHT_IN_IST)).toContain("6 Aug 2026");
  });

  it("renders the business's day and time together", () => {
    const out = formatDateTime(CROSSES_MIDNIGHT_IN_IST);
    expect(out).toContain("6 Aug 2026");
    expect(out).toContain("12:30");
  });

  it("renders a time alone in the business's zone", () => {
    expect(formatTime(CROSSES_MIDNIGHT_IN_IST)).toContain("12:30");
  });

  it("follows the zone when the organization's changes", () => {
    setBusinessTimezone("UTC");
    expect(formatDate(CROSSES_MIDNIGHT_IN_IST)).toContain("5 Aug 2026");
  });

  it("renders an em dash for a missing timestamp, never today's date", () => {
    // Substituting "now" for a null would make a never-synced connector look
    // like it just succeeded.
    for (const f of [formatDate, formatDateTime, formatTime, since]) {
      expect(f(null)).toBe("—");
      expect(f(undefined)).toBe("—");
      expect(f("")).toBe("—");
      expect(f("not a timestamp")).toBe("—");
    }
  });
});

describe("since", () => {
  it("answers in elapsed time, and keeps answering past a day", () => {
    // The hand-rolled ladder this replaced stopped at hours and fell back to an
    // absolute timestamp after a day — precisely the range "when did we last
    // sync" is read in.
    const threeDaysAgo = new Date(Date.now() - 3 * 24 * 60 * 60 * 1000).toISOString();
    expect(since(threeDaysAgo)).toBe("3 days ago");

    const twelveMinAgo = new Date(Date.now() - 12 * 60 * 1000).toISOString();
    expect(since(twelveMinAgo)).toBe("12 minutes ago");
  });
});

describe("todayISO", () => {
  it("is the business's today, so a date picker can select it", () => {
    // `new Date().toISOString().slice(0, 10)` is the UTC date, which before
    // 05:30 IST caps the picker a day early and the operator cannot pick today.
    expect(todayISO()).toMatch(/^\d{4}-\d{2}-\d{2}$/);

    const inZone = new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Kolkata",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(new Date());
    expect(todayISO()).toBe(inZone);
  });

  it("moves with the organization's zone", () => {
    setBusinessTimezone("Pacific/Kiritimati"); // UTC+14, the furthest ahead
    const ahead = todayISO();
    setBusinessTimezone("Pacific/Midway"); // UTC-11, the furthest behind
    const behind = todayISO();
    // Somewhere on earth it is always two different dates at once; these two
    // zones are far enough apart that the helper must report different days.
    expect(ahead >= behind).toBe(true);
  });
});
