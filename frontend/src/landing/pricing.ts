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

/** Which price list a visitor sees. INTL is the default and the one baked into
 *  the prerendered HTML: the repositioning is aimed at US and European
 *  mid-market distributors, and a visitor who is served the Indian list by
 *  accident anchors on a number that is not the offer being made to them. */
export type Region = "IN" | "INTL";

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
  /** PLACEHOLDERS — none of these three strings is a real price yet.
   *
   *  `{{PRICE_TIER_1_USD}}` — Commercial Intelligence. Intended range
   *  $1,500–$2,500 per month; the founder confirms the final number.
   *  `{{PRICE_TIER_2_USD}}` — Platform. Intended $3,500+ per month.
   *  `{{PRICE_CATALOG_BUILD_USD}}` — the one-time catalog build, which has an
   *  Indian price (₹4,999) and no dollar price yet. It is a placeholder rather
   *  than a conversion, because a converted rupee price is a guess dressed as
   *  a decision.
   *
   *  They are rendered verbatim so an unreplaced one is unmissable on the page
   *  rather than plausible; `scripts/prerender.mjs` also lists every `{{…}}`
   *  token it finds in the built HTML at the end of a build. */
  INTL: {
    region: "INTL",
    currency: "USD",
    locale: "en-US",
    unitDecimals: 2,
    tierIntelligence: "{{PRICE_TIER_1_USD}}",
    tierPlatform: "{{PRICE_TIER_2_USD}}",
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

/** Where the visitor is, as far as a browser can honestly say.
 *
 * The clock, not the language: `navigator.language` is what somebody chose to
 * read in and travels with them, while the IANA zone is set from the machine's
 * own location and is the closest thing a static page gets to "which price
 * list is this person actually being sold". A US buyer who reads in `en-IN`
 * must not be shown rupees, which is why the zone wins outright and the
 * language is consulted only where there is no zone at all.
 *
 * Never throws and never returns a maybe: any failure is INTL, which is also
 * what the prerendered HTML already says, so the worst case is that the page
 * does not change after it loads.
 */
export function detectRegion(): Region {
  try {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    // Asia/Calcutta is the older alias and is still what some browsers report.
    if (zone === "Asia/Kolkata" || zone === "Asia/Calcutta") return "IN";
    if (zone) return "INTL";
  } catch {
    /* no Intl, or a locked-down environment: fall through to the language */
  }
  try {
    const tags = [
      ...(navigator.languages ?? []),
      navigator.language,
    ].filter(Boolean) as string[];
    if (tags.some((tag) => /-IN\b/i.test(tag))) return "IN";
  } catch {
    /* no navigator (server render): INTL, which is what is baked in */
  }
  return "INTL";
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
