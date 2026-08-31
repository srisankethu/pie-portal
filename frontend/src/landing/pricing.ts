/** What the landing page shows a visitor, and in which currency.
 *
 * Two things live here and they are here for different reasons.
 *
 * **The prices.** `backend/app/entitlements.py` deliberately holds none —
 * "Prices are deliberately not here — they are marketing copy and live in one
 * place, the landing page's pricing section" — so this module is that place.
 * A second copy of a price is worse than a second copy of a feature list,
 * because the second copy is the one a customer reads.
 *
 * **The worked example.** The decision card in the hero and the arithmetic
 * under the pricing panels describe the *same* line, and they only mean
 * anything if the figures agree with each other and with the formula the
 * product actually applies: floor = cost / (1 − margin floor). Written out
 * twice in prose, they drifted the moment somebody edited one of them.
 * `pricing.test.ts` re-derives every figure here and fails if it does not
 * hold. The line is illustrative — it is not any customer's, and the page says
 * so — but an illustrative figure that is arithmetically wrong is a page
 * arguing with the product it describes.
 *
 * Nothing here is a computed *commercial* number: no price on a real quote is
 * decided by this file, and the platform's own numbers still come from
 * `commercial/` (see CLAUDE.md §1). This is display copy with a consistency
 * check on it.
 *
 * Currency is chosen per visitor rather than per organization, which is why
 * `src/money.ts` is not reused: that module formats what the *signed-in*
 * organization trades in, out of module-level state a session sets. Nobody is
 * signed in here, there is no session to ask, and pointing the app-wide
 * formatter at a guess made from a browser clock would be a worse bug than the
 * duplication it avoids. The formatting below is nine lines and touches
 * nothing outside this file.
 */

import { filled } from "./content";
// One definition of "where is this visitor", shared with the sign-up form —
// see `src/region.ts`. Re-exported because this module's callers have always
// imported it from here, and because a page that shows dollars must not sign
// somebody up into a rupee tenant.
import { detectRegion, type Region } from "../region";

export { detectRegion, type Region };



/** The margin floor the worked example is drawn against — 15%, the figure the
 *  card's own numbers were chosen from. A policy in the product, a constant
 *  here, and the test holds the card's floor to it. */
export const MARGIN_FLOOR = 0.15;

/** One quote line, as the hero card shows it and the pricing note re-reads it. */
export interface WorkedLine {
  /** Units asked for. */
  units: number;
  /** What the customer asked to pay per unit. */
  asked: number;
  /** What the unit costs — shown because the card depicts the *approver's*
   *  view. A salesperson never sees this field, in the product or here. */
  cost: number;
  /** cost / (1 − MARGIN_FLOOR), to the currency's own precision. */
  floor: number;
  /** What PIE recommends, above the floor. */
  recommended: number;
}

export interface RegionPricing {
  region: Region;
  /** ISO 4217, for `Intl.NumberFormat`. */
  currency: "USD" | "INR";
  /** The grouping a reader of this list expects: 4,00,000 in India is a real
   *  difference from 400,000, not a cosmetic one. */
  locale: string;
  /** Decimal places on a unit price. Rupees are whole here; dollars are not,
   *  and a per-unit dollar figure without cents reads as rounded-off. */
  unitDecimals: number;
  /** Monthly, per organization. A `{{…}}` token is a placeholder the founder
   *  must replace before this page is deployed — see the comment on
   *  `PRICING.INTL`. */
  tierIntelligence: string;
  tierPlatform: string;
  /** One-time, per catalog build. */
  catalogBuild: string;
  line: WorkedLine;
}

const PRICING: Record<Region, RegionPricing> = {
  /** The dollar list. The two monthly tiers are the founder's own figures,
   *  supplied and confirmed; they are not conversions of the rupee prices and
   *  the two lists are not expected to track each other.
   *
   *  `catalogBuild` is still `{{PRICE_CATALOG_BUILD_USD}}`, and deliberately:
   *  the one-time catalog build has an Indian price (₹4,999) and no dollar
   *  price, so the sentence that carries it is dropped whole for a dollar
   *  visitor rather than filled with a converted rupee figure — a conversion
   *  is a guess dressed as a decision. A `{{…}}` token is rendered verbatim so
   *  an unreplaced one is unmissable rather than plausible, and
   *  `scripts/prerender.mjs` fails the build if one ever reaches a page. */
  INTL: {
    region: "INTL",
    currency: "USD",
    locale: "en-US",
    unitDecimals: 2,
    tierIntelligence: "$1,950",
    tierPlatform: "$3,950",
    catalogBuild: "{{PRICE_CATALOG_BUILD_USD}}",
    // 38.08 / 0.85 = 44.80 exactly. Chosen that way on purpose: a floor
    // rounded to cents is a floor that has moved, and rounding it *down* — as
    // 44.8235 → 44.82 did — prints a floor fractionally under the policy it
    // came from. An illustrative figure may be illustrative; it may not
    // contradict the rule stated beside it.
    line: { units: 200, asked: 41.2, cost: 38.08, floor: 44.8, recommended: 48.7 },
  },
  IN: {
    region: "IN",
    currency: "INR",
    locale: "en-IN",
    unitDecimals: 0,
    tierIntelligence: "₹9,999",
    tierPlatform: "₹19,999",
    catalogBuild: "₹4,999",
    // 391 / 0.85 = 460 exactly, for the reason given on the dollar line.
    line: { units: 200, asked: 412, cost: 391, floor: 460, recommended: 500 },
  },
};

export function pricingFor(region: Region): RegionPricing {
  return PRICING[region];
}

function format(p: RegionPricing, amount: number, decimals: number): string {
  return new Intl.NumberFormat(p.locale, {
    style: "currency",
    currency: p.currency,
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(amount);
}

/** A per-unit price: `$41.20`, `₹412`. */
export function unitPrice(p: RegionPricing, amount: number): string {
  return format(p, amount, p.unitDecimals);
}

/** A whole-line amount: `$724`, `₹7,200`. Cents on a total this size are
 *  noise, and the two figures it sits between are both round. */
export function lineTotal(p: RegionPricing, amount: number): string {
  return format(p, amount, 0);
}

/** What holding the line to its floor was worth: the ledger's own figure.
 *
 * The ledger counts a movement **up to the floor** and no further, because
 * clearing a floor by more than it asked for is the salesperson's judgement
 * and not something the guardrail did (`backend/app/attribution/`). This is
 * that number, and `heldToRecommended` below is the one the page deliberately
 * does *not* claim — it is quoted only to say which of the two is counted. */
export function heldToFloor(p: RegionPricing): number {
  return (p.line.floor - p.line.asked) * p.line.units;
}

export function heldToRecommended(p: RegionPricing): number {
  return (p.line.recommended - p.line.asked) * p.line.units;
}

/** A tier's price, or what to say instead.
 *
 * A price is the one slot on this site that cannot simply disappear when it is
 * unset: a pricing panel with no price where a price belongs is a worse page
 * than one that says how pricing works. So this is the single exception to
 * "hide the block" — the panel stays and the figure is replaced by the thing
 * that is true of it either way.
 *
 * "Priced per organization" is not a hedge invented for the gap. It is what
 * the section standfirst already says, what `entitlements.py` describes (a
 * plan is requested and a person confirms it — there is no checkout), and it
 * is followed on every panel by a button that starts exactly that
 * conversation. A visitor who reads it knows what to do next, which is all a
 * price was going to tell them here.
 */
export function tierPrice(p: RegionPricing, tier: "intelligence" | "platform"): {
  amount: string | null;
  label: string;
  period: string | null;
} {
  const raw = tier === "intelligence" ? p.tierIntelligence : p.tierPlatform;
  const amount = filled(raw);
  if (amount === null) {
    return { amount: null, label: "Priced per organization", period: null };
  }
  return {
    amount,
    label: amount,
    period: tier === "platform" ? " /month +" : " /month",
  };
}

/** The one-time catalog build price, or `null` where there is none to state.
 *  The sentence that carries it is dropped rather than half-written. */
export function catalogBuildPrice(p: RegionPricing): string | null {
  return filled(p.catalogBuild);
}
