// The money formatter, and the specific mistakes it was written to end.
//
// `money.ts` replaced four separate `inr()` helpers. The tests worth having are
// therefore not "does it put a symbol on a number" but the properties that made
// consolidating worthwhile: that the grouping follows the organization rather
// than the browser, that a `Decimal` serialized as a string formats the same as
// the equivalent number, and that a bad value renders as an em dash instead of
// `NaN` on a dashboard.
import { beforeEach, describe, expect, it } from "vitest";

import { count, money, moneyCurrency, moneySymbol, setMoneyCurrency } from "./money";

// The module holds the currency at module scope, so each test states the
// currency it means rather than inheriting whatever ran before it.
beforeEach(() => setMoneyCurrency("INR"));

describe("money", () => {
  it("groups rupees the Indian way, not the Western way", () => {
    // 4,00,000 vs 400,000 — the difference this module exists for. A test that
    // only checked for "₹" would pass with en-US grouping.
    expect(money(400000)).toContain("4,00,000");
    expect(money(400000)).not.toContain("400,000");
  });

  it("follows the organization's currency into its own grouping", () => {
    setMoneyCurrency("USD");
    expect(moneyCurrency()).toBe("USD");
    // en-US: three-digit groups throughout.
    expect(money(400000)).toContain("400,000");
  });

  it("formats a Decimal-as-string identically to the number", () => {
    // Several endpoints serialize money as a string to avoid the float
    // round-trip, so both shapes genuinely arrive at this function.
    expect(money("400000")).toBe(money(400000));
    expect(money("1234.56")).toBe(money(1234.56));
  });

  it("renders an em dash rather than NaN for a missing or unusable value", () => {
    // A KPI tile reading "NaN" is worse than one reading "—": the first looks
    // like a broken number, the second says there isn't one.
    expect(money(null)).toBe("—");
    expect(money(undefined)).toBe("—");
    expect(money("not a number")).toBe("—");
    expect(money(Number.NaN)).toBe("—");
    expect(money(Number.POSITIVE_INFINITY)).toBe("—");
  });

  it("shows whole units — paise in a KPI is noise", () => {
    expect(money(1234.4)).toBe(money(1234));
    expect(money(1234.6)).toBe(money(1235));
  });

  it("survives an unrecognised ISO code instead of taking the screen down", () => {
    // `build()` catches the Intl throw deliberately. A label must never be the
    // reason a dashboard fails to render.
    expect(() => setMoneyCurrency("NOTACURRENCY")).not.toThrow();
    expect(() => money(1000)).not.toThrow();
    expect(money(1000)).not.toBe("—");
  });

  it("ignores an empty or unchanged currency", () => {
    setMoneyCurrency("USD");
    setMoneyCurrency("");
    setMoneyCurrency(null);
    setMoneyCurrency(undefined);
    expect(moneyCurrency()).toBe("USD");
  });

  it("normalises the currency code's case and padding", () => {
    setMoneyCurrency("  usd  ");
    expect(moneyCurrency()).toBe("USD");
  });

  it("exposes a bare symbol for a field prefix", () => {
    expect(moneySymbol()).toContain("₹");
  });
});

describe("count", () => {
  it("groups a quantity like money but never carries a currency symbol", () => {
    // A table showing ₹4,00,000 beside 400,000 pieces is the bug this prevents:
    // same grouping, no symbol.
    expect(count(400000)).toBe("4,00,000");
    expect(count(400000)).not.toContain("₹");
  });

  it("follows the organization's locale", () => {
    setMoneyCurrency("USD");
    expect(count(400000)).toBe("400,000");
  });

  it("renders an em dash for a missing count", () => {
    expect(count(null)).toBe("—");
    expect(count(undefined)).toBe("—");
    expect(count(Number.NaN)).toBe("—");
  });
});
