// The options on a line, on both cards.
//
// Four things are pinned here, and they fail in four different ways.
//
// **Both roles get the block, and the desk's list is a different object rather
// than this component's filter.** `considerations.for_operations` builds it from
// an allowlist on the server, so an option resting on the purchase ledger is
// absent from a salesperson's payload — not hidden here. The test that matters
// is therefore the shape of the desk's card, not a guard it does not have.
//
// **Every option computed for a line is drawn, including one that interrupted
// nobody.** `surfaces` is on the payload and is deliberately not read: the card
// is the interruption and a card somebody opened is not one, which is the same
// line `WorkingCapitalBlock` draws with `interrupts`.
//
// **A rejection goes through the dismissal path that already exists.** There is
// no button in this block and no reason vocabulary of its own: the option names
// the line, the card's own Dismiss opens the one dialog, and the reasons come
// from `GET /reasons`. A second path would split the one labelled dataset this
// engine has into two nobody can join.
//
// **A refusal is content.** Most lines support no option at all — that is the
// design. A block that drew nothing would read as "nothing to do here", which is
// indistinguishable from "nothing was looked at" and is `CLAUDE.md` §1's
// *absence of evidence is not a pass* wearing a layout.
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DiagnosisCard, OwnerDiagnosisCard } from "./DiagnosisCard";
import type {
  AttributionView, ConsiderationView, ConsiderationsView, IntentView,
  OperationsDiagnosisView, OwnerDiagnosisView, WorkingCapitalView,
} from "./DiagnosisCard";

const REASONS = [
  { code: "COMPARISON_IS_WRONG", label: "The comparable transactions are not comparable" },
];

/** The option both roles are offered, worded exactly as `considerations.py`
 *  writes it. Nothing in this file composes a sentence. */
const SHARED: ConsiderationView = {
  code: "CHECK_THE_COMPARISON",
  label: "Check whether these past transactions are comparable",
  detail:
    "Some of the past transactions behind this range were set aside — as "
    + "outliers, or because it is not clear when they became visible — so the "
    + "range may not be describing a population. Checking whether they are "
    + "comparable at all is the option; if they are not, saying so is what "
    + "tunes this.",
  line_id: "L1",
  rests_on: ["EVIDENCE_WITHHELD", "BELOW_HISTORICAL_RANGE"],
  strength_word: "Strong",
  // Every allowlisted option publishes no magnitude. `null`, and not
  // `NEGLIGIBLE`: "this finding is not a movement" is a different answer.
  severity: null,
  surfaces: true,
};

/** The one the desk never sees, because it rests on the purchase ledger. It is
 *  outside `OPERATIONS_CONSIDERATIONS`, so it is absent from that payload
 *  rather than removed from it. */
const RESTRICTED: ConsiderationView = {
  code: "REVIEW_THE_PURCHASE_SOURCE",
  label: "Review the purchase source for this item",
  detail:
    "The cost level this line faces sits above what its own purchase history "
    + "supports, and that was on record before this quote was written. "
    + "Reviewing where this item is bought is the option. No supplier is "
    + "named: these books record what was paid, not that another source would "
    + "be cheaper.",
  line_id: "L1",
  rests_on: ["KNOWN_COST_CHANGE"],
  strength_word: "Moderate",
  severity: "MAJOR",
  // Computed and available on a card somebody opened, and it interrupted
  // nobody. It is still drawn.
  surfaces: false,
};

const WEIGHED =
  "2 options rest on what this line's evidence already showed "
  + "(CHECK_THE_COMPARISON, REVIEW_THE_PURCHASE_SOURCE); 1 put in front of a "
  + "reader. Each is an option to weigh and none of them is an instruction — "
  + "the pricing decision is not the engine's to make.";

/** The ordinary line: nothing was found worth interrupting anybody about, and
 *  the engine says so rather than saying nothing. */
const NOTHING: ConsiderationsView = {
  line_id: "L1",
  renders: true,
  reason: "NOTHING_TO_WEIGH",
  items: [],
  note: "Nothing on this line was found worth interrupting anybody about, so "
        + "no option is put in front of you. What was checked, and what could "
        + "not be, is on the card.",
};

/** A row read back from the store, which has no column for any of this. */
const NOT_STORED: ConsiderationsView = {
  line_id: "L1",
  renders: true,
  reason: "NOT_ON_STORED_RECORD",
  items: [],
  note: "This is the diagnosis as it was stored, and the options it would "
        + "support are not among its columns. Re-assess this line to see them.",
};

function options(over: Partial<ConsiderationsView> = {}): ConsiderationsView {
  return {
    line_id: "L1", renders: true, reason: "OFFERED",
    items: [SHARED, RESTRICTED], note: WEIGHED,
    ...over,
  };
}

const INTENT: IntentView = {
  read: false, reason: "NO_SOURCE_RECORD", renders: true, headline: "",
  lines: [], codes: [],
  note: "No document from a source system was found.",
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
    context: ["EVIDENCE_WITHHELD"],
    headline: "Below this customer's historical pricing",
    quoted: "₹850",
    historical: "₹1,000",
    evidence: "Strong",
    evidence_detail: "12 comparable transactions.",
    why: "This customer has purchased this item 12 times.",
    note: "",
    qualification: "Historical prices may include exceptional deals.",
    actions: ["REVIEW_PRICE", "DISMISS"],
    intent: INTENT,
    // The desk's own list. `REVIEW_THE_PURCHASE_SOURCE` is not in it, and the
    // point is that it never arrived rather than that it was taken out.
    considerations: options({ items: [SHARED] }),
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
    context: ["EVIDENCE_WITHHELD"],
    headline: "Below this customer's historical pricing",
    lines: ["Quoted ₹850 per unit against a supported range of ₹1,000."],
    opportunity: "No opportunity on this line.",
    evidence: "12 usable, 2 excluded.",
    codes: ["BELOW_HISTORICAL_RANGE"],
    qualification: "Historical prices may include exceptional deals.",
    actions: ["REVIEW_PRICE", "DISMISS"],
    attribution: ATTRIBUTION,
    working_capital: WORKING_CAPITAL,
    intent: INTENT,
    considerations: options(),
    ...over,
  };
}

describe("the options on a line", () => {
  it("are on both cards, in the same words the engine wrote", () => {
    const desk = render(
      <DiagnosisCard diagnosis={deskView()} reasons={REASONS} />);
    expect(within(desk.container).getByText(SHARED.label)).toBeTruthy();
    expect(within(desk.container).getByText(SHARED.detail)).toBeTruthy();
    desk.unmount();

    const owner = render(
      <OwnerDiagnosisCard diagnosis={ownerView()} reasons={REASONS} />);
    expect(within(owner.container).getByText(SHARED.label)).toBeTruthy();
    expect(within(owner.container).getByText(SHARED.detail)).toBeTruthy();
  });

  it("never put the option that rests on the purchase ledger in front of the desk",
     () => {
       render(<DiagnosisCard diagnosis={deskView()} reasons={REASONS} />);

       expect(screen.queryByText(RESTRICTED.label)).toBeNull();
       expect(screen.queryByText(RESTRICTED.detail)).toBeNull();
       // And the whole card carries none of the vocabulary that option is
       // written in. The server is what guarantees this; the assertion is here
       // because a component that reached for `considerations.items` on an
       // un-narrowed payload is exactly how it would stop being true.
       expect(document.body.textContent).not.toMatch(/purchase history/i);
     });

  it("are drawn whether or not they interrupted anybody", () => {
    // `REVIEW_THE_PURCHASE_SOURCE` has `surfaces: false` on this fixture. The
    // card is the interruption; a card somebody opened is not one, so the
    // option is on it.
    render(<OwnerDiagnosisCard diagnosis={ownerView()} reasons={REASONS} />);

    expect(screen.getByText(RESTRICTED.label)).toBeTruthy();
    expect(screen.getByText(RESTRICTED.detail)).toBeTruthy();
  });

  it("keep the order the server fixed rather than sorting by what interrupts",
     () => {
       const { container } = render(
         <OwnerDiagnosisCard diagnosis={ownerView()} reasons={REASONS} />);

       const text = container.textContent ?? "";
       expect(text.indexOf(SHARED.label))
         .toBeLessThan(text.indexOf(RESTRICTED.label));
     });

  it("grade what is graded and say nothing where nothing was published", () => {
    render(<OwnerDiagnosisCard diagnosis={ownerView()} reasons={REASONS} />);

    // `severity: "MAJOR"` is a chip; `severity: null` draws none. `null` is not
    // `NEGLIGIBLE` — drawing a chip for it would report a fact about a field on
    // a document as a margin of nearly zero.
    expect(screen.getAllByText("MAJOR").length).toBe(1);
    expect(screen.queryByText("NEGLIGIBLE")).toBeNull();
    // Confidence is a labelled word rather than a second chip, so the two
    // cannot be read as one ramp.
    expect(screen.getByText(/Confidence in this: Strong/)).toBeTruthy();
    expect(screen.getByText(/Confidence in this: Moderate/)).toBeTruthy();
  });

  it("say what was weighed, and never that the engine is telling anybody what to do",
     () => {
       const { container } = render(
         <OwnerDiagnosisCard diagnosis={ownerView()} reasons={REASONS} />);

       expect(within(container).getByText(WEIGHED)).toBeTruthy();
       expect(container.textContent).toContain(
         "the pricing decision is not the engine's to make");
     });
});

describe("a line with no option", () => {
  it("says what was weighed rather than drawing nothing", () => {
    render(<DiagnosisCard
      diagnosis={deskView({ considerations: NOTHING })}
      reasons={REASONS}
    />);

    expect(screen.getByText(NOTHING.note)).toBeTruthy();
    // The heading is printed either way, so the block is a thing on the screen
    // rather than the absence of one.
    expect(screen.getByText("What you could do about this")).toBeTruthy();
  });

  it("says a stored row has none rather than that there are none", () => {
    render(<OwnerDiagnosisCard
      diagnosis={ownerView({ considerations: NOT_STORED })}
      reasons={REASONS}
    />);

    expect(screen.getByText(NOT_STORED.note)).toBeTruthy();
  });

  it("names the absence when even the reason is missing, and claims nothing else",
     () => {
       // A payload served before this key existed. The block must not take the
       // Quote Builder down, and must not say there is nothing to do.
       render(<DiagnosisCard
         diagnosis={deskView({ considerations: undefined })}
         reasons={REASONS}
       />);

       expect(screen.getByText(
         "What this line's evidence supports was not weighed, and nothing on "
         + "this card says why.")).toBeTruthy();
     });
});

describe("rejecting an option", () => {
  it("goes through the dismissal path the card already has, and no other", () => {
    const onDismiss = vi.fn();
    render(<DiagnosisCard
      diagnosis={deskView()}
      reasons={REASONS}
      onDismiss={onDismiss}
    />);

    // There is exactly one way to say a finding is wrong on this card, and the
    // options block adds no second one.
    const buttons = screen.getAllByRole("button")
      .map((b) => b.textContent ?? "");
    expect(buttons.filter((t) => /dismiss/i.test(t)).length).toBe(1);

    fireEvent.click(screen.getByRole("button", { name: /Dismiss/ }));
    // The vocabulary is the served one — the options block mints none.
    fireEvent.mouseDown(screen.getByRole("combobox"));
    fireEvent.click(screen.getByRole("option", { name: REASONS[0].label }));
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));

    expect(onDismiss).toHaveBeenCalledWith("qd_1", "COMPARISON_IS_WRONG", "");
  });

  it("is posted against the line the option names", () => {
    // The option's `line_id` is the card's own line, which is what makes the
    // existing endpoint the right target: there is nothing else for a rejection
    // to point at.
    expect(SHARED.line_id).toBe(deskView().line_id);
    expect(options().items.every((c) => c.line_id === "L1")).toBe(true);
  });
});
