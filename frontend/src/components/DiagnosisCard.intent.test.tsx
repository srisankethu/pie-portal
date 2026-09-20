// What the record says about a quote, on both cards.
//
// Four things are pinned here, and they fail in four different ways.
//
// **Both roles get it, and they get the same words.** This is the one block on
// the owner's card that is not restricted: every sentence in it is a fact about
// a field on the source document, so a salesperson told the quote was recorded
// as a tender has the reason a price is low without being given a number. The
// first test draws both cards from one payload and requires the sentences to
// match.
//
// **The sentence that is a margin claim arrives from the server or not at all.**
// The component never composes it, never infers it from the codes, and has no
// branch that could add it — the owner's `lines` simply has one more entry,
// because `render_owner` had an object to read it from that a salesperson's
// payload does not have.
//
// **Four statuses stay four sentences.** The server writes one per concept and
// this file renders each one verbatim. A component that summarised them, or
// dropped the ones that say "nothing was declared", would be collapsing the
// distinction the whole feature turns on.
//
// **A refusal is content.** A quote drafted in the builder has no source
// document to read. A block that drew nothing there would read as "no reason was
// recorded", which is a claim about the quote rather than about this card's
// input — `CLAUDE.md` §1's *absence of evidence is not a pass* wearing a layout.
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DiagnosisCard, OwnerDiagnosisCard } from "./DiagnosisCard";
import type {
  AttributionView, IntentView, OperationsDiagnosisView, OwnerDiagnosisView,
  WorkingCapitalView,
} from "./DiagnosisCard";

const REASONS = [{ code: "PRICE_IS_CORRECT", label: "The price is right" }];

/** The four sentences, one per concept, exactly as `intent.py` writes them. */
const FOUR = [
  "This quote records the quote type as TENDER.",
  "This quote has a declared field for the sourcing reason and holds nothing "
  + "in it.",
  "This quote's field for the urgency holds “ASAP”, which this "
  + "organization's declaration does not give a meaning for.",
  "No field on this quote has been declared to carry the account's standing, "
  + "so nothing is recorded about it either way.",
];

const QUALIFICATION =
  "Read from the fields this quote's own system holds, through this "
  + "organization's declaration of what they mean — never inferred from "
  + "free text and never by a model. Absence of a recorded reason is not "
  + "evidence that there was no reason.";

/** The claim the owner's copy leads with. Written by the server; this file
 *  never builds one and the desk's payload never carries one. */
const CLAIM =
  "This line is below the range this customer's own history supports, and the "
  + "field this organization records a pricing reason "
  + "in is empty on this quote. Read that as a potential margin leakage and no "
  + "further — an unrecorded reason is not an absent one, and nothing on "
  + "the record says why this price was set.";

function reading(over: Partial<IntentView> = {}): IntentView {
  return {
    read: true,
    reason: "READ",
    renders: true,
    headline: "Recorded on this quote: the quote type is TENDER.",
    lines: FOUR,
    codes: ["PRICING_REASON_RECORDED", "PRICING_REASON_UNRECOGNISED"],
    note: QUALIFICATION,
    ...over,
  };
}

/** A quote that exists here and in no ERP — the ordinary state of a draft in
 *  the builder, and the case this block is written around. */
const NO_SOURCE_RECORD: IntentView = {
  read: false,
  reason: "NO_SOURCE_RECORD",
  renders: true,
  headline: "",
  lines: [],
  codes: [],
  note: "No document from a source system was found for this quote, so there "
        + "are no source fields to read. What a quote records about why it was "
        + "priced is read from the record its own system holds, and a quote "
        + "drafted here has none yet.",
};

const ATTRIBUTION: AttributionView = {
  renders: true, reason: "NO_COST_BASELINE", headline: "", drivers: [],
  note: "No purchase was knowable.",
};

const WORKING_CAPITAL: WorkingCapitalView = {
  assessed: false, reason: "NO_RATE", interrupts: false, renders: true,
  headline: "", figures: [], severity: "", strength_word: "",
  note: "No annual cost of capital is set.",
};

function deskView(over: Partial<OperationsDiagnosisView> = {}): OperationsDiagnosisView {
  return {
    view: "OPERATIONS",
    quote_diagnosis_id: "qd_1",
    line_id: "L1",
    renders: true,
    comparable: true,
    strength_word: "Strong",
    context: [],
    headline: "Below this customer's historical pricing",
    quoted: "₹850",
    historical: "₹1,000",
    evidence: "Strong",
    evidence_detail: "12 comparable transactions.",
    why: "This customer has purchased this item 12 times.",
    note: "",
    qualification: "Historical prices may include exceptional deals.",
    actions: ["REVIEW_PRICE"],
    intent: reading(),
    ...over,
  };
}

function ownerView(over: Partial<OwnerDiagnosisView> = {}): OwnerDiagnosisView {
  return {
    view: "OWNER",
    quote_diagnosis_id: "qd_1",
    line_id: "L1",
    renders: true,
    comparable: true,
    strength_word: "Strong",
    context: [],
    headline: "Below this customer's historical pricing",
    lines: ["Quoted ₹850 per unit against a supported range of ₹1,000."],
    opportunity: "No opportunity on this line.",
    evidence: "12 usable, 0 excluded.",
    codes: ["BELOW_HISTORICAL_RANGE"],
    qualification: "Historical prices may include exceptional deals.",
    actions: ["REVIEW_PRICE"],
    attribution: ATTRIBUTION,
    working_capital: WORKING_CAPITAL,
    intent: reading(),
    ...over,
  };
}

describe("what the record says about this quote", () => {
  it("is on both cards, in the same words", () => {
    const desk = render(
      <DiagnosisCard diagnosis={deskView()} reasons={REASONS} />);
    for (const line of FOUR) {
      expect(within(desk.container).getByText(line)).toBeTruthy();
    }
    expect(within(desk.container).getByText(reading().headline)).toBeTruthy();
    desk.unmount();

    const owner = render(
      <OwnerDiagnosisCard diagnosis={ownerView()} reasons={REASONS} />);
    for (const line of FOUR) {
      expect(within(owner.container).getByText(line)).toBeTruthy();
    }
    expect(within(owner.container).getByText(reading().headline)).toBeTruthy();
  });

  it("keeps the four statuses as four distinct sentences", () => {
    // The distinction the feature turns on: "no field was declared for this" is
    // not "the field is empty" is not "the field holds something nobody
    // declared a meaning for". Each is on the screen, separately.
    render(<DiagnosisCard diagnosis={deskView()} reasons={REASONS} />);

    expect(screen.getByText(/records the quote type as TENDER/)).toBeTruthy();
    expect(screen.getByText(/declared field for the sourcing reason and holds nothing/))
      .toBeTruthy();
    expect(screen.getByText(/does not give a meaning for/)).toBeTruthy();
    expect(screen.getByText(/has been declared to carry the account's standing/))
      .toBeTruthy();
  });

  it("prints the qualification that keeps the headline from being a verdict", () => {
    render(<DiagnosisCard diagnosis={deskView()} reasons={REASONS} />);
    expect(screen.getByText(QUALIFICATION)).toBeTruthy();
  });

  it("never says a quote had no reason, only that none was recorded", () => {
    const { container } = render(
      <DiagnosisCard
        diagnosis={deskView({
          intent: reading({
            headline: "No pricing reason has been recorded for this quote.",
            lines: [FOUR[1]],
            codes: ["NO_PRICING_REASON_RECORDED"],
          }),
        })}
        reasons={REASONS}
      />);

    const text = (container.textContent ?? "").toLowerCase();
    expect(text).toContain("no pricing reason has been recorded for this quote.");
    for (const claim of ["unintentional", "had no reason", "for no reason",
                         "leakage"]) {
      expect(text.includes(claim), `${claim} is a claim this card must not make`)
        .toBe(false);
    }
  });

  it("shows the margin claim only where the server put one, and never builds one", () => {
    // The owner's payload carries one more sentence, first. The desk's payload
    // for the same quote carries the four and nothing else — the server decided
    // that, from a field a salesperson's projection has no object to hold.
    const claimed = reading({
      headline: "No pricing reason has been recorded for this quote.",
      lines: [CLAIM, FOUR[1]],
      codes: ["NO_PRICING_REASON_RECORDED", "POSSIBLE_MARGIN_LEAKAGE"],
    });

    const owner = render(
      <OwnerDiagnosisCard diagnosis={ownerView({ intent: claimed })}
                          reasons={REASONS} />);
    expect(within(owner.container).getByText(CLAIM)).toBeTruthy();
    owner.unmount();

    // The same quote as a salesperson is served it: the four sentences the
    // server chose to send, and no claim assembled from the codes beside them.
    const desk = render(
      <DiagnosisCard
        diagnosis={deskView({
          intent: reading({
            headline: claimed.headline,
            lines: [FOUR[1]],
            codes: ["NO_PRICING_REASON_RECORDED"],
          }),
        })}
        reasons={REASONS}
      />);
    const text = desk.container.textContent ?? "";
    expect(text).toContain(FOUR[1]);
    expect(text.toLowerCase()).not.toContain("leakage");
    expect(text).not.toContain("POSSIBLE_MARGIN_LEAKAGE");
  });

  it("says nothing could be read rather than drawing nothing", () => {
    const { container } = render(
      <DiagnosisCard diagnosis={deskView({ intent: NO_SOURCE_RECORD })}
                     reasons={REASONS} />);

    // The refusal is on the screen as an alert — a thing, not the absence of
    // one. An empty block here would read as "no reason was recorded", which is
    // a statement about the quote and not about this card's input.
    const alerts = within(container).getAllByRole("alert");
    expect(alerts.some((a) => a.textContent?.includes(
      "No document from a source system was found"))).toBe(true);
    // And no sentence is invented to fill the gap.
    expect(container.textContent).not.toContain("No pricing reason has been");
  });

  it("reads the server's flag rather than counting sentences", () => {
    // A quote whose organization has declared nothing is read successfully and
    // has four sentences saying exactly that. Asking "are there lines" would
    // file a real reading under refusal — the `_identity_candidate` lesson.
    const declaredNothing = reading({
      headline: "No pricing reason has been recorded for this quote.",
      lines: [FOUR[3]],
      codes: ["PRICING_REASON_NOT_DECLARED"],
      note: QUALIFICATION + " Nothing has been declared for this system's "
            + "quotes, so no field on this quote is read as a pricing reason "
            + "and every quote on this book reads the same way until somebody "
            + "declares one.",
    });
    const { container } = render(
      <DiagnosisCard diagnosis={deskView({ intent: declaredNothing })}
                     reasons={REASONS} />);

    expect(within(container).getByText(FOUR[3])).toBeTruthy();
    // It is a reading, so it is not drawn as a refusal.
    const alerts = within(container).queryAllByRole("alert");
    expect(alerts.some((a) => a.textContent?.includes("until somebody declares one")))
      .toBe(false);
  });

  it("survives a payload served before the key existed", () => {
    // The prop is optional although the field is not, for the reason the other
    // two blocks' are: a property read on an older payload would otherwise take
    // the whole Quote Builder down, and "nothing was read" is the true thing to
    // say about it.
    const { intent: _dropped, ...older } = deskView();
    const { container } = render(
      <DiagnosisCard diagnosis={older as OperationsDiagnosisView}
                     reasons={REASONS} />);

    expect(container.textContent).toContain(
      "This quote's own record was not read, and nothing on this card says why.");
    // And its wording shares no phrase with the two blocks below it, so three
    // refusals on one card stay three distinguishable sentences.
    expect(container.textContent).not.toContain("no reason was given");
  });

  it("words nothing of its own, and adds and drops nothing", () => {
    // Two cards differing only by two of the four sentences. If the rendered
    // text differs by exactly those two strings then the component printed what
    // it was given, verbatim and in order: it did not summarise, re-order into
    // prose, gloss `TENDER` into "tender enquiry", or drop the sentence that
    // says nothing was declared for a concept.
    const all = render(
      <OwnerDiagnosisCard diagnosis={ownerView()} reasons={REASONS} />);
    const withAll = all.container.textContent ?? "";
    all.unmount();

    const two = render(
      <OwnerDiagnosisCard
        diagnosis={ownerView({ intent: reading({ lines: FOUR.slice(0, 2) }) })}
        reasons={REASONS}
      />);
    const withTwo = two.container.textContent ?? "";

    expect(withAll.replace(FOUR[2], "").replace(FOUR[3], "")).toBe(withTwo);
    // And the comparison found something, or it proves nothing.
    expect(withAll.length).toBeGreaterThan(withTwo.length);
  });
});
