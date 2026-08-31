/** Where a visitor is, and what money they are therefore quoted and billed in.
 *
 * One definition, read by two places that must not disagree: the landing
 * page's price list and the sign-up form's currency. A visitor shown dollars
 * who then signs up into a rupee tenant has been told two different things,
 * and only one of them is recoverable — nothing in the product changes an
 * organization's currency after sign-up.
 */
import { describe, expect, it } from "vitest";

import { SIGNUP_CURRENCIES, defaultCurrency, detectRegion } from "./region";

describe("detectRegion", () => {
  it("returns INTL where nothing can be resolved", () => {
    // vite.config.ts pins the suite's zone to America/New_York, which is also
    // the case that matters most: a non-Indian zone is never IN.
    expect(detectRegion()).toBe("INTL");
  });

  it("reads India off the clock, not off the language", () => {
    const real = Intl.DateTimeFormat;
    const intl = Intl as { DateTimeFormat: typeof Intl.DateTimeFormat };
    const withZone = (timeZone: string) =>
      (() => ({ resolvedOptions: () => ({ timeZone }) })) as unknown as typeof Intl.DateTimeFormat;
    try {
      intl.DateTimeFormat = withZone("Asia/Kolkata");
      expect(detectRegion()).toBe("IN");
      intl.DateTimeFormat = withZone("Asia/Calcutta");   // the older alias
      expect(detectRegion()).toBe("IN");
      // A buyer in Chicago who reads in en-IN is not billed in rupees.
      intl.DateTimeFormat = withZone("America/Chicago");
      expect(detectRegion()).toBe("INTL");
    } finally {
      intl.DateTimeFormat = real;
    }
  });
});

describe("the currency a new organization is offered", () => {
  it("follows the region, and defaults to dollars outside India", () => {
    expect(defaultCurrency("INTL")).toBe("USD");
    expect(defaultCurrency("IN")).toBe("INR");
  });

  it("only ever preselects something the form can actually offer", () => {
    // A default that is not in the list renders as an empty select, and the
    // organization is created on whatever the API falls back to — which is the
    // exact defect this field exists to fix.
    const codes = SIGNUP_CURRENCIES.map((c) => c.code);
    for (const region of ["IN", "INTL"] as const) {
      expect(codes).toContain(defaultCurrency(region));
    }
  });

  it("offers the markets the product is sold into, each labelled", () => {
    const codes = SIGNUP_CURRENCIES.map((c) => c.code);
    expect(codes).toEqual(["USD", "EUR", "GBP", "INR"]);
    expect(new Set(codes).size).toBe(codes.length);
    for (const { code, label } of SIGNUP_CURRENCIES) {
      // ISO 4217, and a label a person can pick from rather than a bare code.
      expect(code).toMatch(/^[A-Z]{3}$/);
      expect(label).toContain(code);
      expect(label.length).toBeGreaterThan(code.length + 2);
    }
  });
});
