// The diagnosis card, rendered.
//
// ## What this claims, and what it does not
//
// The guarantee that cost never reaches a salesperson is proven on the server —
// `tests/decision_platform/test_quote_diagnosis_api.py` sweeps the whole
// serialised payload and walks the quoted price across the purchase cost — and
// nothing here replaces it. The payload this component receives is built from a
// record type with no cost, margin, opportunity or peer field on it, so there is
// no `{mgmt && …}` guard in the component and none is wanted.
//
// What this file pins is the layer below that: the card is **silent unless the
// server says otherwise**, every sentence on it came from the server already
// written, and a dismissal cannot be sent without a reason. The first of those
// is the one that decays quietly — a caller that ignored `renders` would be
// overriding a threshold decision made against a versioned policy, from a place
// that has no idea what the policy is.
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DiagnosisCard } from "./DiagnosisCard";
import type { OperationsDiagnosisView } from "./DiagnosisCard";

const REASONS = [
  { code: "PRICE_IS_CORRECT", label: "The price is right for this deal" },
  { code: "VOLUME_COMMITMENT", label: "Priced for a volume or contract commitment" },
];

function view(over: Partial<OperationsDiagnosisView> = {}): OperationsDiagnosisView {
  return {
    view: "OPERATIONS",
    quote_diagnosis_id: "qd_1",
    line_id: "L1",
    renders: true,
    comparable: true,
    strength_word: "Strong",
    context: [],
    headline: "Below this customer's historical pricing",
    quoted: "₹900",
    historical: "₹980 – ₹1,020",
    evidence: "Strong",
    evidence_detail: "14 comparable transactions, all in the last 12 months",
    why: "This customer has purchased this item 14 times at a comparable quantity, between ₹980 and ₹1,020.",
    note: "",
    qualification: "Historical prices may include exceptional deals not recorded in the ERP.",
    actions: ["REVIEW_PRICE", "DISMISS"],
    ...over,
  };
}

describe("the card is silent by default", () => {
  it("renders nothing when the server says it does not surface", () => {
    const { container } = render(
      <DiagnosisCard diagnosis={view({ renders: false })} reasons={REASONS} />);

    expect(container).toBeEmptyDOMElement();
  });

  it("renders the server's own sentences when it does", () => {
    render(<DiagnosisCard diagnosis={view()} reasons={REASONS} />);

    expect(screen.getByText("Below this customer's historical pricing")).toBeTruthy();
    expect(screen.getByText("₹900")).toBeTruthy();
    expect(screen.getByText("₹980 – ₹1,020")).toBeTruthy();
    expect(screen.getByText("Strong")).toBeTruthy();
    expect(screen.getByText(/purchased this item 14 times/)).toBeTruthy();
  });
});

describe("the qualification", () => {
  it("is on every card, without exception", () => {
    render(<DiagnosisCard diagnosis={view()} reasons={REASONS} />);

    expect(screen.getByText(/may include exceptional deals/)).toBeTruthy();
  });
});

describe("a cost-driven card", () => {
  it("carries the sentence and no figure", () => {
    // The server's wording for the case the specification's own example
    // template printed an acquisition cost for.
    render(<DiagnosisCard reasons={REASONS} diagnosis={view({
      headline: "Margin on this line is compressed by supply cost, not by your price. No price change needed.",
      why: "What this customer pays is in line with what they have paid before. The pressure on this line is on the supply side.",
    })} />);

    expect(screen.getByText(/compressed by supply cost/)).toBeTruthy();
    // The selling prices are present and must be: they are what this customer
    // has paid, and the desk decides with them. What is absent is any figure
    // that came from the buy side — the card has no field to carry one, so the
    // only numbers on it are the two it was handed.
    const shown = document.body.textContent ?? "";
    const figures = [...shown.matchAll(/₹[\d,]+/g)].map((m) => m[0]);
    expect(new Set(figures)).toEqual(new Set(["₹900", "₹980", "₹1,020"]));
  });
});

describe("dismissal", () => {
  it("cannot be sent without a reason", () => {
    const onDismiss = vi.fn();
    render(<DiagnosisCard diagnosis={view()} reasons={REASONS}
                          onDismiss={onDismiss} />);

    fireEvent.click(screen.getByRole("button", { name: /Dismiss — reason\?/ }));
    const confirm = screen.getByRole("button", { name: "Dismiss" });

    expect(confirm.hasAttribute("disabled")).toBe(true);
    fireEvent.click(confirm);
    expect(onDismiss).not.toHaveBeenCalled();
  });

  it("sends the reason code the server serves, not a label", () => {
    const onDismiss = vi.fn();
    render(<DiagnosisCard diagnosis={view()} reasons={REASONS}
                          onDismiss={onDismiss} />);

    fireEvent.click(screen.getByRole("button", { name: /Dismiss — reason\?/ }));
    fireEvent.mouseDown(screen.getByRole("combobox", { name: /Reason/ }));
    fireEvent.click(screen.getByRole("option", {
      name: "Priced for a volume or contract commitment" }));
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));

    expect(onDismiss).toHaveBeenCalledWith("qd_1", "VOLUME_COMMITMENT", "");
  });

  it("is unavailable when the diagnosis was never recorded", () => {
    // `record: false` on the assess call returns a card with no id. There is
    // nothing to attach a dismissal to, and a button that posts to a null id is
    // a 404 somebody meets in front of a customer.
    render(<DiagnosisCard reasons={REASONS} onDismiss={vi.fn()}
                          diagnosis={view({ quote_diagnosis_id: null })} />);

    expect(screen.getByRole("button", { name: /Dismiss — reason\?/ })
      .hasAttribute("disabled")).toBe(true);
  });
});

describe("the actions the server offers", () => {
  it("are the only ones drawn", () => {
    render(<DiagnosisCard reasons={REASONS}
                          diagnosis={view({ actions: ["REVIEW_PRICE"] })} />);

    expect(screen.getByRole("button", { name: "Review price" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Dismiss/ })).toBeNull();
  });
});
