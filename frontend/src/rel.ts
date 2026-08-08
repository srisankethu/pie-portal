import type { Tone } from "./platform/kit";

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
