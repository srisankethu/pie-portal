/** One morning's work, as one list.
 *
 * Three screens used to hold it. `/approvals` held the prices waiting on a
 * person, `/decisions` held the situations the platform had found, and
 * `/unanswered-quotes` held the single highest-value data entry in the product
 * — and all three were somewhere to *go*, which is why the third had two
 * hundred rows in it. Nothing said what to do first, because nothing could:
 * they were three lists with three rankings and no way to interleave them.
 *
 * This is the interleave, and it is deliberately not arithmetic.
 *
 * **Why there is no cross-kind score.** It is tempting to rank everything by
 * what it costs to leave alone, and the prototype's copy says exactly that. But
 * an approval's cost is a quote held up, a decision's is a modelled annual
 * bleed, and an unanswered quote's is not a cost at all — it is a fact nobody
 * recorded. Dividing those into one number would mean inventing the exchange
 * rate between them here, in the browser, which is the one thing §1 says the
 * interface never does. So the order is a **policy, stated in one place**:
 *
 *   1. Approvals this person can decide — somebody is waiting and a quote
 *      cannot be sent until they answer. Oldest ask first.
 *   2. Decisions, in the server's own ranked order. That ranking is computed,
 *      versioned and auditable; re-sorting it here would throw away the only
 *      ordering in this product that can be shown working.
 *   3. Three outcome questions. Three, because the pile is ~215 and a morning
 *      is not — a worklist you cannot finish is one nobody starts. The rest are
 *      named on the done screen and at `/unanswered-quotes`, which still holds
 *      the whole pile for anybody who wants to work it.
 *
 * Approvals a reader cannot decide are still shown, at the end, read-only: a
 * salesperson's own request is the one thing they most want the status of, and
 * hiding it would send them to ask in person.
 *
 * **Nothing here computes money.** Every figure is a string the server already
 * rendered or a number it already computed; this file formats and orders.
 */
import { money } from "../../money";
import { formatDate, since } from "../../when";
import { factLabel, factValue, isPrimaryFact, TYPE_LABEL } from "../format";
import type { Tone } from "../kit";
import { PATH, pathFor } from "../route";
import type {
  ApprovalRequest, DecisionDetail, Fact, UnrecordedQuote,
} from "../types";

export type QueueKind = "approval" | "decision" | "outcome";

/** One thing a person can do about an item, named as the verb it is.
 *
 *  `Act on this` was the old queue's button, on a page of prose, and it did not
 *  say what acting was. A label here carries the number: *Approve ₹298*,
 *  *Reprice to ₹545*, *Won*. */
export interface QueueAction {
  /** What the screen dispatches on. Per kind: an approval status, a decision
   *  action key, or an outcome status. */
  key: string;
  label: string;
  tone: "primary" | "danger" | "good" | "ghost";
}

/** A line of the arithmetic. Values arrive rendered — see the header. */
export interface QueueRow {
  label: string;
  value: string;
  /** True where the server marked this fact as withheld from this reader. */
  restricted?: boolean;
}

export interface QueueItem {
  id: string;
  kind: QueueKind;
  /** What kind of thing this is, in the words a reader uses. */
  kindLabel: string;
  tone: Tone;
  /** The rail's one line. */
  short: string;
  /** Where it came from and when, under the title. */
  meta: string;
  title: string;
  body: string;
  /** The headline figure, already formatted, or null where this reader may not
   *  read it — which is a different thing from there not being one, and the
   *  screen says which. */
  cost: string | null;
  costLabel: string;
  costNote?: string;
  rows: QueueRow[];
  /** Written by a model from the figures above, where there is one. */
  interpretation?: string | null;
  /** What this item will not answer. Absence of evidence, named. */
  limits: string[];
  links: { label: string; to: string }[];
  actions: QueueAction[];
  /** Set when the item is not this reader's to answer: what is happening
   *  instead, and who it is with. */
  waiting?: string;
  /** An outcome recorded as lost must say which kind of loss it was. The
   *  screen asks with these, in place, rather than in a dialog. */
  reasonPrompt?: string;
}

/* ── approvals ────────────────────────────────────────────────────────────── */

/** What a quote-line approval is about, as the server sends it.
 *
 *  `subject` is `Record<string, unknown>` on the wire and carries cost and
 *  margin — so it is **absent for a salesperson**, not blanked here. Read
 *  defensively: a key that is missing is a key the server withheld or never
 *  had, and both mean the same thing to this file. */
function subjectRows(req: ApprovalRequest): QueueRow[] {
  const s = (req.subject ?? {}) as Record<string, unknown>;
  const out: QueueRow[] = [];
  const push = (label: string, v: unknown, as: "money" | "pct" | "plain") => {
    if (v === null || v === undefined || v === "") return;
    const n = typeof v === "string" ? Number(v) : v;
    if (as === "money" && typeof n === "number" && Number.isFinite(n)) {
      out.push({ label, value: money(n) });
    } else if (as === "pct" && typeof n === "number" && Number.isFinite(n)) {
      out.push({ label, value: `${(n * 100).toFixed(1)}%` });
    } else {
      out.push({ label, value: String(v) });
    }
  };
  push("Item", s.product ?? s.product_label, "plain");
  push("Quantity", s.qty, "plain");
  push("Rate asked", s.proposed_price, "money");
  push("Landed cost", s.unit_cost, "money");
  push("Margin at that rate", s.proposed_margin, "pct");
  push("Floor", s.margin_floor, "pct");
  push("Rate that clears the floor", s.floor_price, "money");
  return out;
}

/** The value held up by this line, where the server sent enough to say so.
 *
 *  Quantity × price is multiplication of two numbers the server computed, not a
 *  commercial figure this file derives: it is the same arithmetic the quote
 *  itself prints on the line, and it is shown only when both arrive. Where
 *  either is missing the item says so rather than rendering a zero. */
function lineValue(req: ApprovalRequest): string | null {
  const s = (req.subject ?? {}) as Record<string, unknown>;
  const qty = Number(s.qty);
  const price = Number(s.proposed_price);
  if (!Number.isFinite(qty) || !Number.isFinite(price)) return null;
  return money(qty * price);
}

export function approvalItem(req: ApprovalRequest): QueueItem {
  const mine = req.can_decide;
  const value = lineValue(req);
  return {
    id: `approval:${req.approval_request_id}`,
    kind: "approval",
    kindLabel: req.required_authority === "OWNER"
      ? "Approval · blocking a quote" : "Approval",
    tone: "bad",
    short: req.title,
    meta: [`asked by ${req.requested_by}`,
           req.requested_at ? since(req.requested_at) : null]
      .filter(Boolean).join(" · "),
    title: req.title,
    body: req.summary,
    cost: value,
    costLabel: "Line value held up",
    costNote: "the whole quote waits on this",
    rows: subjectRows(req),
    // The reason travels with the request, and it is the half of this a
    // salesperson can see on their own ask.
    interpretation: req.reason ? `Their reason: ${req.reason}` : null,
    limits: [],
    links: [
      { label: "Open the quote", to: pathFor("quotes", req.subject_id) },
      { label: "Every approval", to: PATH.approvals },
    ],
    actions: mine
      ? [
          { key: "APPROVED", label: "Approve", tone: "primary" },
          { key: "CHANGES_REQUESTED", label: "Ask for a change", tone: "ghost" },
          { key: "REJECTED", label: "Refuse", tone: "danger" },
        ]
      : [],
    waiting: mine
      ? undefined
      : req.cannot_decide_reason
        ?? "This one is not yours to answer. It is with whoever can.",
  };
}

/* ── decisions ────────────────────────────────────────────────────────────── */

/** Which decisions read as red rather than amber.
 *
 *  The band is the server's, and this only picks the ink for it. HIGH is the
 *  one that should look like it costs something to ignore. */
function decisionTone(band: string): Tone {
  if (band === "HIGH") return "bad";
  if (band === "MEDIUM") return "warn";
  return "neutral";
}

function factRows(facts: Fact[]): QueueRow[] {
  return facts
    .filter((f) => isPrimaryFact(f.label))
    .slice(0, 6)
    .map((f) => ({
      label: factLabel(f.label),
      value: factValue(f.label, f.value),
      restricted: f.restricted,
    }));
}

/** A decision's own action vocabulary, as verbs.
 *
 *  The server publishes `actions` per decision; the four keys below are the
 *  ones `ACTION_META` in `PlatformApp` knows how to record, and a key it does
 *  not know is dropped rather than rendered as a button that does nothing. */
const DECISION_ACTION_TONE: Record<string, QueueAction["tone"]> = {
  accept: "primary", modify: "ghost", escalate: "ghost", dismiss: "danger",
};

export function decisionItem(d: DecisionDetail): QueueItem {
  const label = TYPE_LABEL[d.decision_type] ?? d.decision_type;
  const interp = d.interpretation;
  return {
    id: `decision:${d.decision_id}`,
    kind: "decision",
    kindLabel: `Decision · ${label.toLowerCase()}`,
    tone: decisionTone(d.priority?.band ?? ""),
    short: d.subject_label,
    meta: [d.subject_origin?.company, d.detected_at ? `found ${since(d.detected_at)}` : null]
      .filter(Boolean).join(" · "),
    title: interp?.title ?? `${label} · ${d.subject_label}`,
    body: interp?.explanation ?? d.rationale ?? "",
    // `financial` is a decimal string the server computed and `basis` says what
    // the number *is* — a figure without that sentence invites the reader to
    // assume the wrong claim, so the two travel together or neither does.
    cost: d.impact?.financial ? money(d.impact.financial) : null,
    costLabel: "If left alone",
    costNote: d.impact?.basis,
    rows: factRows(d.facts ?? []),
    interpretation: interp?.recommendation ?? null,
    // The caveat and the confidence reasons are the same claim in two places:
    // what this will not answer. Both, where both exist.
    limits: [interp?.caveat, ...(d.confidence?.reasons ?? [])].filter(
      (x): x is string => Boolean(x)),
    links: [
      { label: "The whole decision, with its trail", to: pathFor("detail", d.decision_id) },
      { label: "The evidence behind it", to: PATH.evidence },
    ],
    actions: (d.actions ?? [])
      .filter((a) => a.key in DECISION_ACTION_TONE)
      .map((a) => ({ key: a.key, label: a.label, tone: DECISION_ACTION_TONE[a.key] })),
  };
}

/* ── outcome questions ────────────────────────────────────────────────────── */

/** How many unanswered quotes a morning is asked about.
 *
 *  The pile is in the hundreds. Three is a number somebody finishes, and
 *  finishing is what makes the next morning's three get answered too. */
export const OUTCOMES_PER_MORNING = 3;

export function outcomeItem(q: UnrecordedQuote): QueueItem {
  const age = q.days_past_expiry;
  return {
    id: `outcome:${q.quote_document_ref}`,
    kind: "outcome",
    kindLabel: age === null
      ? "One question · no outcome recorded"
      : `One question · ${age} days past expiry`,
    tone: "neutral",
    short: `Did ${q.customer_label} answer ${q.number ?? "this quote"}?`,
    meta: [q.number, `raised ${formatDate(q.raised_on)}`,
           q.value === null ? "no total on the quote" : money(q.value)]
      .filter(Boolean).join(" · "),
    title: `Did ${q.customer_label} ever come back on this quote?`,
    body: "The ERP holds no outcome for it. Answering takes a second, and it is "
        + "the only thing that makes a win rate honest — three of these a "
        + "morning, not the whole pile at once.",
    cost: q.value === null ? null : money(q.value),
    costLabel: "Quote value",
    costNote: q.value === null
      ? "the ERP recorded no total for this one"
      : "what was put in front of the customer",
    rows: [
      { label: "Raised", value: formatDate(q.raised_on) },
      { label: "Expires", value: q.expires_on ? formatDate(q.expires_on) : "Not recorded" },
      { label: "The ERP's own status", value: q.source_status },
      { label: "Opened", value: q.opened_at ? formatDate(q.opened_at) : "No open recorded" },
    ],
    interpretation: null,
    limits: q.opened_at
      ? []
      : ["Whether they read it — no open was recorded, which is not the same as "
         + "never opened"],
    links: [
      ...(q.customer_id
        ? [{ label: `${q.customer_label}'s account`, to: pathFor("customer", q.customer_id) }]
        : []),
      { label: "The whole pile", to: PATH.unrecordedQuotes },
    ],
    actions: [
      { key: "WON", label: "Won", tone: "good" },
      { key: "LOST", label: "Lost", tone: "danger" },
      { key: "STILL_OPEN", label: "Still open", tone: "ghost" },
    ],
    reasonPrompt: "Why was it lost? Only what you actually know.",
  };
}

/* ── the order ────────────────────────────────────────────────────────────── */

/** This morning, hardest first — see the header for why this is a policy and
 *  not a score. */
export function buildQueue({
  approvals, decisions, unanswered,
}: {
  approvals: ApprovalRequest[];
  /** In the server's ranked order, as `listDecisions` returned them. */
  decisions: DecisionDetail[];
  unanswered: UnrecordedQuote[];
}): QueueItem[] {
  const open = approvals.filter((r) => r.is_open);
  const byAge = (a: ApprovalRequest, b: ApprovalRequest) =>
    (a.requested_at ?? "").localeCompare(b.requested_at ?? "");
  const mine = open.filter((r) => r.can_decide).sort(byAge).map(approvalItem);
  const theirs = open.filter((r) => !r.can_decide).sort(byAge).map(approvalItem);

  return [
    ...mine,
    ...decisions.map(decisionItem),
    ...unanswered.slice(0, OUTCOMES_PER_MORNING).map(outcomeItem),
    // Last, and read-only: a request of your own that somebody else must answer
    // is a status, not a task.
    ...theirs,
  ];
}
