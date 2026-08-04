// Rendering an already-computed amount as money. Shared by both apps in this
// repo — see src/Tip.tsx for the same arrangement and the same reason.
//
// This replaced four separate `inr()` helpers (rel.ts, platform/format.ts,
// AdminScreens.tsx, CommercialScreens.tsx), each of which glued a "₹" onto a
// number and each of which would have needed finding and editing the first time
// this platform was pointed at a customer who does not trade in rupees. Four
// copies of a formatting rule is four chances to render the same figure two
// different ways on two screens.
//
// NO business logic here. Rounding for *display* is presentation; rounding that
// changes a quoted price is a commercial decision and lives in the backend
// (`commercial/`), which is the only thing allowed to produce a number.

// The currency the signed-in organization trades in. Set once from the server's
// policy payload — the client is never the authority on this, it is only told.
// A module-level value rather than a prop threaded through fifteen components,
// for the same reason a locale is: one session belongs to one organization, and
// a component deep in a table has no business knowing where the answer came
// from.
let currency = "INR";

// Indian digit grouping (4,00,000) is a real difference from Western grouping
// (400,000), not a cosmetic one, so the locale has to follow the currency.
// Pinned explicitly rather than left to the browser: the same figure must read
// the same way for every user of an organization, whatever their machine says.
const LOCALE: Record<string, string> = {
  INR: "en-IN",
  PKR: "en-PK",
  BDT: "en-BD",
  LKR: "en-LK",
  NPR: "en-NP",
};

let format = build(currency);

function build(code: string): Intl.NumberFormat {
  try {
    return new Intl.NumberFormat(LOCALE[code] ?? "en-US", {
      style: "currency",
      currency: code,
      maximumFractionDigits: 0,
    });
  } catch {
    // An unrecognised ISO code must not take a screen down over a label.
    return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
  }
}

/** Point the formatter at the organization's currency. Idempotent. */
export function setMoneyCurrency(code: string | null | undefined): void {
  const next = (code ?? "").trim().toUpperCase();
  if (!next || next === currency) return;
  currency = next;
  format = build(next);
}

export function moneyCurrency(): string {
  return currency;
}

/** The bare symbol, for a field prefix where a full amount would not fit. */
export function moneySymbol(): string {
  const parts = format.formatToParts(0);
  return parts.find((p) => p.type === "currency")?.value ?? currency + " ";
}

/** A plain count, grouped the same way money is.
 *
 * Separate from `money` because a quantity is not an amount and must not carry
 * a currency symbol — but the *grouping* still has to follow the organization,
 * or a table shows `4,00,000` of money beside `400,000` of pieces. */
export function count(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "—";
  return new Intl.NumberFormat(LOCALE[currency] ?? "en-US", {
    maximumFractionDigits: 0,
  }).format(n);
}

/** `₹4,00,000` / `$400,000`. Whole units — paise in a KPI is noise.
 *
 * Accepts a numeric string as well as a number: money is `Decimal` on the
 * server and several endpoints serialize it as a string to avoid the float
 * round-trip, so both shapes genuinely arrive here. Anything that is not a
 * finite number renders as an em dash rather than as `NaN`. */
export function money(n: number | string | null | undefined): string {
  const value = typeof n === "string" ? Number(n) : n;
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return format.format(Math.round(value));
}
