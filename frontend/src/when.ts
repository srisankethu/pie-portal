// Rendering a timestamp in the business's own timezone.
//
// The exact arrangement `money.ts` uses, for the same reason and after the same
// mistake. Six components had their own date helper — IdentityScreen,
// DataScreen, SyncStatus, ConnectionsPanel, AdminScreens, CommercialScreens —
// and all six passed no timezone, so every one of them rendered in *the
// browser's* zone.
//
// That is wrong here in a way it is not wrong in a consumer app. The server
// stores UTC. A sync that finished at 00:30 IST is 19:00 the previous day in
// UTC, and a browser in London shows a different day again. Three people
// looking at the same run disagreed about when it happened, and each of them
// was reading their own machine rather than the business.
//
// The business's day is the one worth showing, so it is pinned from the session
// exactly as the currency is: one session belongs to one organization, the
// server is the authority, and a component deep in a table has no business
// knowing where the answer came from.

let zone = "Asia/Kolkata";

/** Set from the sign-in response, alongside `setMoneyCurrency`. */
export function setBusinessTimezone(tz: string | undefined | null): void {
  const next = (tz || "").trim();
  if (!next) return;
  try {
    // Validate before adopting: an unknown zone would make every subsequent
    // format call throw, and a blank date column is worse than one in the
    // wrong zone.
    new Intl.DateTimeFormat("en-IN", { timeZone: next });
    zone = next;
  } catch {
    /* keep the previous zone */
  }
}

export function businessTimezone(): string {
  return zone;
}

/** A short zone label for a column header — "IST", "GST". */
export function timezoneLabel(): string {
  try {
    const parts = new Intl.DateTimeFormat("en-IN", {
      timeZone: zone, timeZoneName: "short",
    }).formatToParts(new Date());
    return parts.find((p) => p.type === "timeZoneName")?.value ?? zone;
  } catch {
    return zone;
  }
}

function parse(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "5 Aug 2026". Em dash for a missing value, never today's date. */
export function formatDate(iso: string | null | undefined): string {
  const d = parse(iso);
  return d ? d.toLocaleDateString("en-IN", { dateStyle: "medium", timeZone: zone }) : "—";
}

/** "5 Aug 2026, 9:30 pm" — in the business's zone. */
export function formatDateTime(iso: string | null | undefined): string {
  const d = parse(iso);
  return d
    ? d.toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short", timeZone: zone })
    : "—";
}

/** "9:30 pm" — for a timestamp whose date is already established nearby. */
export function formatTime(iso: string | null | undefined): string {
  const d = parse(iso);
  return d ? d.toLocaleTimeString("en-IN", { timeStyle: "short", timeZone: zone }) : "—";
}

/** "just now" / "12 min ago" / "3 h ago", then an absolute date.
 *
 *  Elapsed time needs no timezone — it is a difference between two instants —
 *  but the *absolute* fallback does, which is why it is here rather than
 *  reimplemented next to each caller. */
export function since(iso: string | null | undefined): string {
  const d = parse(iso);
  if (!d) return "—";
  const mins = Math.round((Date.now() - d.getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)} h ago`;
  return formatDateTime(iso);
}

/** Today in the business's zone, as `YYYY-MM-DD` for a date input's `max`.
 *
 *  `new Date().toISOString().slice(0, 10)` is the UTC date, so before 05:30 IST
 *  it caps a date picker a day early and the operator cannot select today. */
export function todayISO(): string {
  // en-CA formats as YYYY-MM-DD, which is what a date input wants.
  return new Date().toLocaleDateString("en-CA", { timeZone: zone });
}
