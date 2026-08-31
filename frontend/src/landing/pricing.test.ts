/** The worked example on the landing page has to be arithmetically true.
 *
 * The hero's decision card and the note under the pricing panels describe one
 * quote line between them — asked, cost, floor, recommended, and what holding
 * it to the floor was worth. Those figures are illustrative, and being
 * illustrative is exactly why nothing else checks them: no screen renders
 * them, no endpoint returns them, and a reader who does the arithmetic is the
 * first thing that would notice they disagree. That reader is a distributor
 * evaluating a product whose whole claim is that its numbers re-derive.
 *
 * So this re-derives them. It fails if somebody edits a price on the card and
 * not the note, or picks a floor that its own cost does not produce.
 */
import { describe, expect, it } from "vitest";
import {
  MARGIN_FLOOR,
  catalogBuildPrice,
  tierPrice,
  detectRegion,
  heldToFloor,
  heldToRecommended,
  lineTotal,
  pricingFor,
  unitPrice,
  type Region,
} from "./pricing";

const REGIONS: Region[] = ["INTL", "IN"];

describe("the worked line", () => {
  it.each(REGIONS)("%s: floor is exactly cost / (1 - margin floor)", (region) => {
    const p = pricingFor(region);
    // Exactly, not "to the currency's precision". A floor rounded down is a
    // floor that has moved below the policy it came from, and a page whose
    // whole argument is that its arithmetic closes cannot print one.
    expect(p.line.floor).toBeCloseTo(p.line.cost / (1 - MARGIN_FLOOR), 9);
  });

  it.each(REGIONS)("%s: the line is below its floor, and the recommendation is above it", (region) => {
    const { line } = pricingFor(region);
    expect(line.asked).toBeLessThan(line.floor);
    expect(line.recommended).toBeGreaterThan(line.floor);
    expect(line.cost).toBeLessThan(line.asked);
  });

  it.each(REGIONS)("%s: what was held is the gap to the floor, times the units", (region) => {
    const p = pricingFor(region);
    expect(heldToFloor(p)).toBeCloseTo((p.line.floor - p.line.asked) * p.line.units, 6);
    // And it is strictly less than pricing at the recommendation would have
    // been — the difference is the whole point of the note that quotes both.
    expect(heldToFloor(p)).toBeLessThan(heldToRecommended(p));
  });

  it("holds the figures the page actually prints", () => {
    // Round numbers were chosen on purpose: a reader checks these in their
    // head, and 723.99 would read as a rounding error rather than as a figure.
    const intl = pricingFor("INTL");
    expect(lineTotal(intl, heldToFloor(intl))).toBe("$720");
    expect(lineTotal(intl, heldToRecommended(intl))).toBe("$1,500");
    expect(unitPrice(intl, intl.line.asked)).toBe("$41.20");
    expect(unitPrice(intl, intl.line.floor)).toBe("$44.80");

    const inr = pricingFor("IN");
    expect(lineTotal(inr, heldToFloor(inr))).toBe("₹9,600");
    expect(lineTotal(inr, heldToRecommended(inr))).toBe("₹17,600");
    expect(unitPrice(inr, inr.line.asked)).toBe("₹412");
    expect(unitPrice(inr, inr.line.floor)).toBe("₹460");
  });
});

describe("the price list", () => {
  it("keeps the rupee prices out of the international list entirely", () => {
    const intl = pricingFor("INTL");
    const shown = [intl.tierIntelligence, intl.tierPlatform, intl.catalogBuild].join(" ");
    // Not "does not equal ₹9,999" — anything rupee-denominated anchors a
    // dollar buyer to a number that is not the offer being made to them.
    expect(shown).not.toMatch(/₹|INR/);
    // And no figure at all outside a placeholder token: the only numerals left
    // are the 1 and 2 in PRICE_TIER_1_USD / PRICE_TIER_2_USD.
    expect(shown.replace(/\{\{[A-Z0-9_]+\}\}/g, "")).not.toMatch(/\d/);
  });

  it("marks every unset international price as a placeholder", () => {
    const intl = pricingFor("INTL");
    for (const price of [intl.tierIntelligence, intl.tierPlatform, intl.catalogBuild]) {
      expect(price).toMatch(/^\{\{[A-Z0-9_]+\}\}$/);
    }
  });

  it("has a real price for every Indian tier", () => {
    const inr = pricingFor("IN");
    for (const price of [inr.tierIntelligence, inr.tierPlatform, inr.catalogBuild]) {
      expect(price).not.toContain("{{");
      expect(price).toMatch(/^₹[\d,]+$/);
    }
  });
});

describe("detectRegion", () => {
  it("returns INTL where nothing can be resolved", () => {
    // jsdom reports America/New_York (vite.config.ts pins TZ for the suite),
    // which is the case that matters most: a non-Indian zone is never IN.
    expect(detectRegion()).toBe("INTL");
  });

  it("reads India off the clock, not off the language", () => {
    const real = Intl.DateTimeFormat;
    // `detectRegion` reads exactly one thing off the formatter, so the fake
    // provides exactly that and nothing else — a fuller stub would only be a
    // second place for this test to be wrong.
    const intl = Intl as { DateTimeFormat: typeof Intl.DateTimeFormat };
    const withZone = (timeZone: string) =>
      (() => ({ resolvedOptions: () => ({ timeZone }) })) as unknown as typeof Intl.DateTimeFormat;
    try {
      intl.DateTimeFormat = withZone("Asia/Kolkata");
      expect(detectRegion()).toBe("IN");
      // The older alias some browsers still report.
      intl.DateTimeFormat = withZone("Asia/Calcutta");
      expect(detectRegion()).toBe("IN");
      // A buyer in Chicago who reads in en-IN is not shown rupees.
      intl.DateTimeFormat = withZone("America/Chicago");
      expect(detectRegion()).toBe("INTL");
    } finally {
      intl.DateTimeFormat = real;
    }
  });
});

describe("a tier whose price nobody has set", () => {
  const intl = pricingFor("INTL");

  it("says how pricing works instead of showing a token", () => {
    // The one slot on this site that cannot simply disappear: a pricing panel
    // with a blank where a price belongs is worse than one that says how
    // pricing is done. The wording is not invented for the gap — it is what
    // the section standfirst and `entitlements.py` both already say.
    for (const tier of ["intelligence", "platform"] as const) {
      const shown = tierPrice(intl, tier);
      expect(shown.amount).toBeNull();
      expect(shown.label).toBe("Priced per organization");
      expect(shown.label).not.toContain("{{");
      // No dangling "/month" under a phrase that is not an amount.
      expect(shown.period).toBeNull();
    }
  });

  it("drops the catalog-build sentence rather than half-writing it", () => {
    expect(catalogBuildPrice(intl)).toBeNull();
  });
});

describe("a tier whose price is set — the Indian list, which is real", () => {
  const inr = pricingFor("IN");

  it("shows the figure and its period", () => {
    expect(tierPrice(inr, "intelligence")).toEqual({
      amount: "₹9,999", label: "₹9,999", period: " /month",
    });
    expect(tierPrice(inr, "platform")).toEqual({
      amount: "₹19,999", label: "₹19,999", period: " /month +",
    });
  });

  it("keeps the catalog-build price", () => {
    expect(catalogBuildPrice(inr)).toBe("₹4,999");
  });
});
