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
import type { DiagnosisView } from "./DiagnosisCard";
import type { QuoteDiagnosisState } from "../useQuoteDiagnosis";

const REASONS = [{ code: "PRICE_IS_CORRECT", label: "The price is right" }];

function view(over: Partial<DiagnosisView> = {}): DiagnosisView {
  return {
    quote_diagnosis_id: null,
    line_id: "L1",
    renders: true,
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
