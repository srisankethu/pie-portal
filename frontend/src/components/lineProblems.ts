/** What is wrong with a quote line, and what would fix it.
 *
 * The builder used to answer both questions somewhere other than the line: a
 * `Commercial` column with a chip in it, a `Status` column with another, a
 * filter chip at the top of the screen saying how many lines were in each
 * state, and — for the one that actually stops a quote — a refusal at the end,
 * after fourteen lines had been built and priced. That is error *reporting*
 * dressed as error prevention: the person finds out at Send, having done all
 * the work, and then has to go and find which rows the sentence was about.
 *
 * So a problem is attached to the line it is about, with its fix as a button,
 * and this module is the one place that decides what a problem is. Two things
 * read it — the grid, which draws a strip under the row, and the summary bar,
 * which counts what is still stopping the send — and they must agree, because
 * "2 things to settle first" beside a grid showing three strips is worse than
 * either number alone.
 *
 * **Nothing here is computed.** Every figure is one the server put on the line
 * or on its assessment; this decides which sentence to show and which button to
 * offer beside it. In particular the fix for a thin price is `line.recommended`
 * — the number the server already serves to both roles as decision support —
 * and it is labelled as what it is. A "rate that clears the floor" computed in
 * this file would be cost ÷ (1 − floor) done in a browser, which is exactly the
 * arithmetic §1 keeps on the server.
 */
import type { Line, LineIntelligence, Quote } from "../types";

export type ProblemTone = "bad" | "warn" | "info";

/** What pressing a fix does. A description rather than a callback, so this
 *  module stays pure and the screen keeps every handler it already had. */
export type Fix =
  | { kind: "accept-reading" }
  | { kind: "choose-candidate"; code: string }
  | { kind: "open-supply" }
  | { kind: "set-price"; price: number }
  | { kind: "ask-approval"; reasonCode: string; reason: string }
  | { kind: "create-item" };

export interface LineFix {
  label: string;
  /** The one this line's problem is most likely answered by. */
  primary?: boolean;
  fix: Fix;
}

export interface LineProblem {
  /** Stable across renders, so React keeps the strip rather than rebuilding it
   *  while somebody is reaching for the button on it. */
  key: string;
  tone: ProblemTone;
  title: string;
  detail: string;
  /** True where this is one of the things that stops the quote being sent.
   *  Advisory problems still annotate their line — a shortfall is worth knowing
   *  before promising a date — but they never hold the send. */
  blocking: boolean;
  fixes: LineFix[];
}

/** How many candidates a strip offers before it stops being a choice and starts
 *  being a list. The rest are behind the supply drawer, which is built for it. */
const CANDIDATES_ON_THE_STRIP = 2;

export function problemsFor(
  line: Line,
  intel: LineIntelligence | undefined,
  opts: {
    /** Whether this reader has cost and margin at all. Decides which of the
     *  server's two sentences an exception is rendered with — never whether the
     *  problem is shown, because the salesperson is the person who has to do
     *  something about it. */
    mgmt: boolean;
    systemShort: string;
    /** An approval already asked for on this line, so the strip says it is
     *  waiting rather than offering to ask again. */
    approvalPending?: boolean;
    readOnly?: boolean;
  },
): LineProblem[] {
  const out: LineProblem[] = [];
  const canFix = !opts.readOnly;

  // A model read this line out of the customer's prose and nobody has checked
  // it. First, because everything below it is a fact about a product that may
  // not be the product they asked for.
  if (line.proposed) {
    out.push({
      key: `${line.id}:reading`,
      tone: "warn",
      title: "Check what was read from this line",
      detail: line.reading
        ? `They wrote “${line.raw}”, read as ${line.reading}.`
        : `They wrote “${line.raw}”.`,
      blocking: true,
      fixes: canFix ? [{ label: "That is right", primary: true, fix: { kind: "accept-reading" } }] : [],
    });
  }

  // No product behind the line. The candidates the engine ranked are offered
  // here rather than only inside the drawer: on a long tender most unresolved
  // lines are answered by the first or second one, and opening a drawer per
  // line to press the top option is the friction this screen is about.
  if (!line.supplyCode) {
    const offered = line.candidates.slice(0, CANDIDATES_ON_THE_STRIP);
    out.push({
      key: `${line.id}:unresolved`,
      tone: "bad",
      title: "Which item is this?",
      detail: line.rel === "PIE_DOWN"
        ? "The resolution engine did not answer, so nothing was matched. The item can still be chosen by hand."
        : offered.length
          ? `Nothing matched closely enough to take automatically. ${offered.length} candidate(s) came back.`
          : "Nothing matched, and there are no candidates to choose from.",
      blocking: true,
      fixes: canFix
        ? [
            ...offered.map((c, i) => ({
              label: `${c.code} · ${c.rel.toLowerCase()}`,
              primary: i === 0,
              fix: { kind: "choose-candidate", code: c.code } as Fix,
            })),
            { label: offered.length ? "Search" : "Choose an item", fix: { kind: "open-supply" } },
          ]
        : [],
    });
  }

  // The deterministic checks. `blocking` and `requires_approval` are the
  // server's, and the sentence is the server's too — twice over, because a
  // manager's copy of it may name cost where a salesperson's may not.
  for (const e of intel?.exceptions ?? []) {
    if (e.severity === "INFO") continue;
    const fixes: LineFix[] = [];
    // The recommended rate, offered as the recommendation it is. It is only a
    // fix where it is above what is on the line — proposing a number the line
    // already beats would be a button that changes nothing.
    if (canFix && line.recommended !== null
        && (line.quoted === null || line.recommended > line.quoted)) {
      fixes.push({
        label: `Use the recommended rate`,
        primary: true,
        fix: { kind: "set-price", price: line.recommended },
      });
    }
    if (canFix && e.requires_approval && !opts.approvalPending) {
      fixes.push({
        label: "Ask for approval",
        fix: { kind: "ask-approval", reasonCode: e.code, reason: e.title },
      });
    }
    out.push({
      key: `${line.id}:${e.code}`,
      tone: e.severity === "CRITICAL" ? "bad" : "warn",
      title: e.title,
      detail: [(opts.mgmt && e.manager_detail) || e.detail,
               opts.approvalPending ? "Asked for — waiting on an answer." : null]
        .filter(Boolean).join(" "),
      blocking: Boolean(intel?.blocking) && e.requires_approval,
      fixes,
    });
  }

  // Not a blocker, and worth saying before somebody promises a date on it.
  const short = Number(line.shortage ?? 0);
  if (line.supplyCode && short > 0) {
    out.push({
      key: `${line.id}:short`,
      tone: "info",
      title: `${short} short against free stock`,
      detail: "It can still be quoted — this is what has to be bought, or promised on a lead time.",
      blocking: false,
      fixes: canFix ? [{ label: "Supply options", fix: { kind: "open-supply" } }] : [],
    });
  }

  // The ledger does not hold this product, so the document cannot name it.
  if (line.supplyCode && line.inBooks === false) {
    out.push({
      key: `${line.id}:books`,
      tone: "warn",
      title: `Not in ${opts.systemShort}`,
      detail: "The quote can be built on it; sending needs the item to exist in the books.",
      blocking: false,
      fixes: canFix ? [{ label: `Create in ${opts.systemShort}`, fix: { kind: "create-item" } }] : [],
    });
  }

  return out;
}

/** One thing standing between this quote and the customer. */
export interface Blocker {
  key: string;
  /** Said in the plural the count needs, because "1 line(s)" is how a screen
   *  tells somebody it was written by a machine. */
  text: string;
}

/** What is still stopping the send, counted the way the grid shows it.
 *
 *  Lines are counted once per *kind* of problem rather than once each: three
 *  unresolved lines are one thing to settle, and telling somebody there are
 *  seven things to do when there are three rows to look at is the summary bar
 *  arguing with the grid.
 *
 *  The gate is the server's answer and outranks anything derived here — it is
 *  added as its own blocker when it refuses for a reason the lines do not
 *  already say. */
export function blockersFor(
  quote: Quote,
  intel: Record<string, LineIntelligence>,
  gateBlockedReason: string | null,
): Blocker[] {
  const out: Blocker[] = [];
  const n = (count: number, one: string, many: string) =>
    `${count} ${count === 1 ? one : many}`;

  const unresolved = quote.lines.filter((l) => !l.supplyCode).length;
  if (unresolved) {
    out.push({ key: "unresolved", text: n(unresolved, "line has no item yet", "lines have no item yet") });
  }

  const proposed = quote.lines.filter((l) => l.proposed).length;
  if (proposed) {
    out.push({ key: "reading", text: n(proposed, "reading to check", "readings to check") });
  }

  const needsApproval = quote.lines.filter((l) => intel[l.id]?.blocking).length;
  if (needsApproval) {
    out.push({
      key: "approval",
      text: n(needsApproval, "price needs signing off", "prices need signing off"),
    });
  } else if (gateBlockedReason) {
    // The gate refused for something the lines above do not account for. Its
    // own sentence, verbatim: it is the only one that knows why.
    out.push({ key: "gate", text: gateBlockedReason });
  }

  if (quote.missingFields.length) {
    out.push({
      key: "fields",
      text: `${n(quote.missingFields.length, "required detail", "required details")}: `
          + quote.missingFields.join(", "),
    });
  }

  if (!quote.customer.trim()) {
    out.push({ key: "customer", text: "no customer chosen" });
  }

  return out;
}

/** What the quote total covers, and what it leaves out.
 *
 *  A subtotal over thirteen of fourteen lines is not wrong, and it is not the
 *  number somebody thinks they are reading. The missing line is missing from
 *  the figure rather than counted as zero — which is the right arithmetic and
 *  the one that needs saying out loud. */
export function coverage(quote: Quote): string | null {
  const total = quote.lines.length;
  if (!total) return null;
  const unpriced = quote.summary.unpriced;
  if (!unpriced) return `Covers all ${total} lines.`;
  return `Covers ${total - unpriced} of ${total} lines. `
       + `${unpriced === 1 ? "The other has" : `The other ${unpriced} have`} no rate, `
       + "so they are missing from this total rather than counted as zero.";
}
