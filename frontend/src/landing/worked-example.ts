/** The one quote line the landing page works through, and in which currency.
 *
 * This module used to hold two things: the price list and the worked example.
 * The price list is gone — **the site states no price, in any currency, on any
 * panel.** What a distributor pays turns on how many companies they run, which
 * ERP each sits on and how much catalogue there is to build, and a panel that
 * printed a figure was answering that question for a reader it had asked
 * nothing. The plans section says what each plan *is* and ends in a form.
 *
 * So what is left is the example, which is a different kind of number and the
 * reason this file still needs a test. The decision card in the hero and the
 * arithmetic in the plans section describe the *same* line, and they only mean
 * anything if the figures agree with each other and with the formula the
 * product actually applies: floor = cost / (1 − margin floor). Written out
 * twice in prose, they drifted the moment somebody edited one of them.
 * `worked-example.test.ts` re-derives every figure here and fails if it does
 * not hold. The line is illustrative — it is not any customer's — but an
 * illustrative figure that is arithmetically wrong is a page arguing with the
 * product it describes.
 *
 * This file used to say "and the page says so", and the page did not. The word
 * "illustrative" appeared in the card's `aria-label` and in a comment; a
 * sighted reader got a quote number, a customer, a policy hash and a revision,
 * drawn as a record and captioned nowhere. That is the same defect the rest of
 * this site spends `{{PLACEHOLDER}}` tokens avoiding — `proof.ts` hides a whole
 * section rather than invent one customer, while the hero above it invented
 * one — so the marker is now visible on the card and pinned by a test.
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

/** The item the worked line is for — a catalogue code and nothing else.
 *
 *  It was "DNMG 150608-MP insert": a real ISO turning-insert designation, the
 *  one the seeded demo book uses, chosen so the card would name something a
 *  distributor recognises. The trouble is *which* distributor. This platform
 *  is sold to distributors of whatever their book holds — the connector
 *  registry alone spans six ERPs and no vertical — and a carbide insert on
 *  the front page tells a fastener or a bearings distributor that the product
 *  was built for somebody else's catalogue. Nothing about the decision on
 *  that card is specific to a cutting tool.
 *
 *  So: a code shaped like a catalogue code, decoding to nothing. It has to
 *  stay that way — the moment it means something in some trade, it is that
 *  trade's page again. */
export const EXAMPLE_ITEM = "Part 4114-08";

/** One quote line, as the hero card shows it and the plans note re-reads it. */
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

export interface RegionExample {
  region: Region;
  /** Who the line is for, in the card's own words.
   *
   *  Here rather than in the JSX because that is where it was, and the money
   *  was here: the region swap changed the currency and left the customer
   *  alone, so a visitor on an Indian clock read “a machine shop in Ohio”
   *  paying ₹412 a unit. Nobody wrote that sentence — it was assembled out of
   *  two halves that no longer agreed, which is the only way a page whose
   *  every figure has a test on it still manages to say something false. One
   *  record now holds both, so they cannot come apart. */
  customer: string;
  /** ISO 4217, for `Intl.NumberFormat`. */
  currency: "USD" | "INR";
  /** The grouping a reader of this line expects: 4,00,000 in India is a real
   *  difference from 400,000, not a cosmetic one. */
  locale: string;
  /** Decimal places on a unit price. Rupees are whole here; dollars are not,
   *  and a per-unit dollar figure without cents reads as rounded-off. */
  unitDecimals: number;
  line: WorkedLine;
}

const EXAMPLES: Record<Region, RegionExample> = {
  INTL: {
    region: "INTL",
    customer: "A machine shop in Ohio",
    currency: "USD",
    locale: "en-US",
    unitDecimals: 2,
    // 38.08 / 0.85 = 44.80 exactly. Chosen that way on purpose: a floor
    // rounded to cents is a floor that has moved, and rounding it *down* — as
    // 44.8235 → 44.82 did — prints a floor fractionally under the policy it
    // came from. An illustrative figure may be illustrative; it may not
    // contradict the rule stated beside it.
    line: { units: 200, asked: 41.2, cost: 38.08, floor: 44.8, recommended: 48.7 },
  },
  IN: {
    region: "IN",
    customer: "A machine shop in Pune",
    currency: "INR",
    locale: "en-IN",
    unitDecimals: 0,
    // 391 / 0.85 = 460 exactly, for the reason given on the dollar line.
    line: { units: 200, asked: 412, cost: 391, floor: 460, recommended: 500 },
  },
};

export function exampleFor(region: Region): RegionExample {
  return EXAMPLES[region];
}

/** Margin at the price the customer asked for — the figure the card flags.
 *
 *  Derived rather than written down, for the reason every other figure here is
 *  derived: the two regions' costs are not proportional to their prices, so a
 *  literal would be right for one card and quietly wrong for the other. */
export function marginAtAsked(p: RegionExample): number {
  return (p.line.asked - p.line.cost) / p.line.asked;
}

/** `5.1%` — the app prints one decimal place, so this does too. */
export function marginPct(p: RegionExample): string {
  return `${(marginAtAsked(p) * 100).toFixed(1)}%`;
}

function format(p: RegionExample, amount: number, decimals: number): string {
  return new Intl.NumberFormat(p.locale, {
    style: "currency",
    currency: p.currency,
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(amount);
}

/** A per-unit price: `$41.20`, `₹412`. */
export function unitPrice(p: RegionExample, amount: number): string {
  return format(p, amount, p.unitDecimals);
}

/** A whole-line amount: `$720`, `₹9,600`. Cents on a total this size are
 *  noise, and the two figures it sits between are both round. */
export function lineTotal(p: RegionExample, amount: number): string {
  return format(p, amount, 0);
}

/** What holding the line to its floor was worth: the ledger's own figure.
 *
 * The ledger counts a movement **up to the floor** and no further, because
 * clearing a floor by more than it asked for is the salesperson's judgement
 * and not something the guardrail did (`backend/app/attribution/`). This is
 * that number, and `heldToRecommended` below is the one the page deliberately
 * does *not* claim — it is quoted only to say which of the two is counted. */
export function heldToFloor(p: RegionExample): number {
  return (p.line.floor - p.line.asked) * p.line.units;
}

export function heldToRecommended(p: RegionExample): number {
  return (p.line.recommended - p.line.asked) * p.line.units;
}
