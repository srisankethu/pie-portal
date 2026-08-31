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


/** What the backend calls a candidate that came from this organization's own
 *  book, rather than from the manufacturer catalogue.
 *
 *  It is `sellable_catalog.SELLABLE_LABEL` on the server, and it reaches the
 *  browser as a candidate's `brand` — `ScoredEquivalent.to_dict` publishes the
 *  catalog label there, not the record's own maker.
 *
 *  **A literal here is a coupling, and it is pinned rather than hoped for.**
 *  If the server renamed its label, every book candidate would quietly stop
 *  being marked: no error, no empty screen, just a distinction silently gone
 *  from a list a person picks a product out of. That is the shape of failure
 *  this codebase keeps finding, so `backend/tests/test_frontend_contract.py`
 *  asserts the two strings are equal and the gate goes red on a rename. */
export const OWN_BOOK_LABEL = "book";

/** Did this candidate come out of the organization's own item master?
 *
 *  The distinction a person acts on. A catalogue candidate is something the
 *  maker lists; a book candidate is something the business already sells, and
 *  the two can appear in one list describing the same physical product —
 *  `query._dedup` keys on a description a book record does not carry, so it
 *  cannot collapse them. Unmarked, they read as two unrelated options. */
export function isFromOwnBook(brand: string | null | undefined): boolean {
  return (brand || "").toLowerCase() === OWN_BOOK_LABEL;
}

/** A candidate's source, in words a reader outside this codebase can use.
 *
 *  "book" is the server's internal label and means nothing to a salesperson;
 *  everything else is a manufacturer's name and already reads correctly. */
export function sourceLabel(brand: string): string {
  return isFromOwnBook(brand) ? "our book" : brand;
}
