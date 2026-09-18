// The diagnosis panel on the Quote Builder.
//
// This file exists because of a gap no other file could see. The engine, the
// endpoints and `DiagnosisCard` all shipped and nothing imported the card, so
// no screen ever rendered a diagnosis — and both suites were green, because the
// backend tested the server and the component suite tested the card in
// isolation. The missing assertion was "somebody can see one", which belonged
// to neither.
//
// So the first test here is that a diagnosis the server surfaced appears. The
// rest are the three states this panel must keep apart: nothing to say, still
// asking, and could not ask — the last of which must never look like the first.
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { QuoteDiagnosisPanel } from "./QuoteDiagnosisPanel";
import type { OperationsDiagnosisView, OwnerDiagnosisView } from "./DiagnosisCard";
import type { QuoteDiagnosisState } from "../useQuoteDiagnosis";

const REASONS = [{ code: "PRICE_IS_CORRECT", label: "The price is right" }];

/** The manager's projection. A different shape carrying the economics, which
 *  is why it is a different fixture and not `view()` with fields bolted on. */
function ownerView(over: Partial<OwnerDiagnosisView> = {}): OwnerDiagnosisView {
  return {
    view: "OWNER",
    quote_diagnosis_id: null,
    line_id: "L1",
    renders: true,
    comparable: true,
    strength_word: "Strong",
    context: [],
    headline: "Above this customer's historical pricing",
    lines: [
      "Quoted \u20b9339 per unit against a supported range of \u20b9218 (median \u20b9218), from 8 comparable transactions knowable on 2026-02-11.",
      "Expected cost \u20b9150 per unit, from 4 purchase(s) on record.",
    ],
    opportunity: "Roughly \u20b96,065 on this line at the top of the band.",
    evidence: "11 usable, 0 excluded.",
    codes: ["ABOVE_HISTORICAL_RANGE"],
    // Owner-only by construction — `DiagnosisCard.AttributionView`. A refusal
    // here rather than a split: this file is about the panel choosing a card,
    // and `DiagnosisCard.attribution.test.tsx` is where the block itself is
    // pinned.
    attribution: {
      renders: false,
      headline: "",
      drivers: [],
      note: "No cost is on record for this item, so the movement could not be "
            + "split between price and cost.",
    },
    // Owner-only for the same reason, and a refusal for the same reason: the
    // block itself is pinned in `DiagnosisCard.workingcapital.test.tsx`, and
    // this is the state a book that has not set a cost of capital is in.
    working_capital: {
      assessed: false,
      interrupts: false,
      renders: true,
      headline: "",
      figures: [],
      severity: "",
      strength_word: "",
      note: "NO_RATE: No annual cost of capital is set, so there is no rate at "
            + "which to charge the money this line ties up.",
    },
    qualification: "Historical prices may include exceptional deals.",
    actions: ["REVIEW_PRICE"],
    ...over,
  };
}

function view(over: Partial<OperationsDiagnosisView> = {}): OperationsDiagnosisView {
  return {
    view: "OPERATIONS",
    quote_diagnosis_id: null,
    line_id: "L1",
    renders: true,
    comparable: true,
    strength_word: "Strong",
    context: [],
    headline: "Below this customer's historical pricing",
    quoted: "₹900",
    historical: "₹980 – ₹1,020",
    evidence: "Strong",
    evidence_detail: "14 comparable transactions",
    why: "This customer has purchased this item 14 times.",
    note: "",
    qualification: "Historical prices may include exceptional deals.",
    actions: ["REVIEW_PRICE"],
    ...over,
  };
}

function state(over: Partial<QuoteDiagnosisState> = {}): QuoteDiagnosisState {
  return { byLineId: {}, loading: false, error: null, ...over };
}

describe("a diagnosis the server surfaced", () => {
  it("is on the screen — the assertion whose absence hid the whole feature", () => {
    render(<QuoteDiagnosisPanel
      lineIds={["L1"]}
      diagnosis={state({ byLineId: { L1: view() } })}
      dismissReasons={REASONS}
      onReviewPrice={vi.fn()} />);

    expect(screen.getByText("Below this customer's historical pricing")).toBeTruthy();
    expect(screen.getByText("₹980 – ₹1,020")).toBeTruthy();
  });

  it("appears once per line that surfaced, and not for the others", () => {
    render(<QuoteDiagnosisPanel
      lineIds={["L1", "L2", "L3"]}
      diagnosis={state({ byLineId: {
        L1: view({ line_id: "L1" }),
        L2: view({ line_id: "L2", renders: false }),
        L3: view({ line_id: "L3", headline: "Third" }),
      } })}
      dismissReasons={REASONS}
      onReviewPrice={vi.fn()} />);

    expect(screen.getAllByText(/historical pricing|Third/)).toHaveLength(2);
  });

  it("follows the grid's filter rather than the whole quote", () => {
    // A card about a line somebody has filtered away is a card about something
    // they cannot see.
    render(<QuoteDiagnosisPanel
      lineIds={["L1"]}
      diagnosis={state({ byLineId: {
        L1: view(), L2: view({ line_id: "L2", headline: "Hidden line" }),
      } })}
      dismissReasons={REASONS}
      onReviewPrice={vi.fn()} />);

    expect(screen.queryByText("Hidden line")).toBeNull();
  });
});

describe("nothing worth saying", () => {
  it("renders no panel at all, not an empty one", () => {
    // The default state on an ordinary quote. A heading over nothing is
    // furniture on every screen where the engine correctly stayed quiet.
    const { container } = render(<QuoteDiagnosisPanel
      lineIds={["L1"]}
      diagnosis={state({ byLineId: { L1: view({ renders: false }) } })}
      dismissReasons={REASONS}
      onReviewPrice={vi.fn()} />);

    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when the server surfaced nothing", () => {
    const { container } = render(<QuoteDiagnosisPanel
      lineIds={["L1"]} diagnosis={state()}
      dismissReasons={REASONS} onReviewPrice={vi.fn()} />);

    expect(container).toBeEmptyDOMElement();
  });
});

describe("could not ask", () => {
  it("says so, rather than looking like a clean quote", () => {
    // The distinction this panel exists to keep: silence because there was
    // nothing to say, and silence because the check never ran. Only one of
    // those is good news, and a reader cannot tell them apart unless told.
    render(<QuoteDiagnosisPanel
      lineIds={["L1"]}
      diagnosis={state({ error: "network down" })}
      dismissReasons={REASONS}
      onReviewPrice={vi.fn()} />);

    expect(screen.getByText(/could not be checked/)).toBeTruthy();
    expect(screen.getByText(/network down/)).toBeTruthy();
  });

  it("is careful to say the quote itself is not what failed", () => {
    render(<QuoteDiagnosisPanel
      lineIds={["L1"]}
      diagnosis={state({ error: "500" })}
      dismissReasons={REASONS}
      onReviewPrice={vi.fn()} />);

    expect(screen.getByText(/Nothing is wrong with the quote/)).toBeTruthy();
  });
});

describe("still asking", () => {
  it("shows a loading state rather than an answer it does not have", () => {
    render(<QuoteDiagnosisPanel
      lineIds={["L1"]} diagnosis={state({ loading: true })}
      dismissReasons={REASONS} onReviewPrice={vi.fn()} />);

    expect(document.querySelector(".MuiSkeleton-root")).toBeTruthy();
  });

  it("keeps the cards it already has while re-asking", () => {
    // Editing a rate re-asks the whole quote. Blanking the panel on every
    // keystroke would make the findings flicker in and out under the price
    // somebody is typing.
    render(<QuoteDiagnosisPanel
      lineIds={["L1"]}
      diagnosis={state({ loading: true, byLineId: { L1: view() } })}
      dismissReasons={REASONS}
      onReviewPrice={vi.fn()} />);

    expect(screen.getByText("Below this customer's historical pricing")).toBeTruthy();
  });
});

describe("whose projection arrived", () => {
  // The gap this closes: the server had been building the owner report since
  // the engine landed, and nothing in the front end drew it. A manager saw no
  // card on the Quote Builder or on an ERP quote — not a thinner card, none —
  // while their own payload said the line was above the customer's history.
  it("draws the manager's card from the owner payload", () => {
    render(<QuoteDiagnosisPanel
      lineIds={["L1"]}
      diagnosis={state({ byLineId: { L1: ownerView() } })}
      dismissReasons={REASONS}
      onReviewPrice={vi.fn()} />);

    expect(screen.getByText("Above this customer's historical pricing")).toBeTruthy();
    // The report's own sentences, laid out rather than re-worded.
    expect(screen.getByText(/supported range of ₹218/)).toBeTruthy();
    expect(screen.getByText(/Expected cost ₹150 per unit/)).toBeTruthy();
    expect(screen.getByText(/Roughly ₹6,065 on this line/)).toBeTruthy();
  });

  it("picks the card from the payload's own view, not from a role prop", () => {
    // Both fixtures say `renders: true` and differ only in `view`. If the panel
    // were choosing any other way, one of these two would render the wrong
    // body — and the operations card reads `quoted`/`why`, which an owner
    // payload does not have, so the failure would be blank fields rather than
    // a leak. The discriminated union is what makes that a type error instead.
    const { unmount } = render(<QuoteDiagnosisPanel
      lineIds={["L1"]} diagnosis={state({ byLineId: { L1: ownerView() } })}
      dismissReasons={REASONS} onReviewPrice={vi.fn()} />);
    expect(screen.queryByText("Why?")).toBeNull();
    expect(screen.getByText("What the evidence says")).toBeTruthy();
    unmount();

    render(<QuoteDiagnosisPanel
      lineIds={["L1"]} diagnosis={state({ byLineId: { L1: view() } })}
      dismissReasons={REASONS} onReviewPrice={vi.fn()} />);
    expect(screen.getByText("Why?")).toBeTruthy();
    expect(screen.queryByText("What the evidence says")).toBeNull();
  });

  it("stays silent for a manager when the server did not surface the line", () => {
    // `renders` governs both cards now. It governed only one before, which is
    // how the two halves came to disagree about what had been flagged.
    render(<QuoteDiagnosisPanel
      lineIds={["L1"]}
      diagnosis={state({ byLineId: { L1: ownerView({ renders: false }) } })}
      dismissReasons={REASONS}
      onReviewPrice={vi.fn()} />);

    expect(screen.queryByText("Above this customer's historical pricing")).toBeNull();
  });
});
