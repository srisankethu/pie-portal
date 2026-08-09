import type { Tone } from "./platform/kit";
import type { Line } from "./types";

/** What to call this line's product when asking the platform about it.
 *
 *  The code, and the supply product's code ahead of the requested one, because
 *  the thing being priced is what will actually ship.
 *
 *  Here for the same reason `relTone` is: two callers, and they had drifted.
 *  `useQuoteIntelligence` asked about `supplyCode || reqCode`; `DecisionSupport`
 *  asked about `supplyDesc || reqDesc || …`, preferring the *description* — so
 *  the two panels of one drawer identified one line differently and each
 *  reported "no history" about a different thing. On an unresolved line it was
 *  worse than inconsistent: `reqDesc` holds the resolver's status message, so
 *  the panel looked up a product called "No PIE match" and told the reader, in
 *  quotation marks, that “No PIE match” was not found in the sales history. */
export function productRef(line: Line): string {
  return line.supplyCode || line.reqCode;
}

/** What a relationship means, as one of the interface's five tones.
 *
 *  Here rather than at each call site: the quote grid and the supply drawer
 *  both render this term, and two mappings would eventually disagree about
 *  whether an ambiguous match is a warning or an error — on two screens showing
 *  the same line. The tone is a *second* cue behind the word, never the only
 *  one; `StatusChip` always prints the label.
 *
 *  This replaced a `REL_STYLE` table of inline `CSSProperties` — background and
 *  colour literals per relationship, applied to a hand-rolled `.chip` span.
 *  Both screens are `StatusChip` now, so the palette comes from the theme and
 *  follows it. */
export function relTone(rel: string): Tone {
  switch (rel) {
    case "EXACT":
      return "good";
    case "TECH":
      return "info";
    case "AMBIGUOUS":
      return "warn";
    case "UNRESOLVED":
    case "INCOMPATIBLE":
      return "bad";
    default:
      // COMPAT, POSSIBLE, PIE_DOWN, NONE — offerable, with a caveat the label
      // itself carries.
      return "neutral";
  }
}

/** A line's blocking status, in the same vocabulary. */
export function statusTone(kind: string): Tone {
  return kind === "technical" ? "bad"
    : kind === "operational" ? "warn"
      : kind === "commercial" ? "info"
        : "good";
}
