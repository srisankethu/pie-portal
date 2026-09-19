// What a quote says about itself as a whole, on the panel that draws it.
//
// **The one this file exists for: a line that loses money is on the screen when
// the quote's total is healthy and that line renders no card.** That is not a
// contrived shape. The price engine compares a price against what this customer
// has paid before, so a line priced exactly as always says nothing and draws
// nothing — and it can still be bought for more than it sells for. A reader with
// two cards in front of them and a 46% margin under the grid would conclude the
// quote was fine. The roll-up names the line, with what it loses.
//
// The backend proves the response carries it (`test_quote_diagnosis_rollup_api`)
// and the engine proves the object does (`test_quote_diagnosis_rollup`). Neither
// could see whether anybody can read it, which is the gap this panel's own first
// test was written about.
//
// **Absence is content, here too.** A quote where nothing is unusual says what
// was checked and what could not be, and stops. A panel that drew nothing would
// read as "all clear", which covers both "every line was judged and none was
// unusual" and "half of them could not be compared against anything" — and only
// one of those is good news.
//
// **And the desk gets none of the money.** Not because this component withholds
// it: a salesperson's response carries no `rollup` key at all, so the panel has
// nothing to guard. The test is the shape of the state, not a guard.
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { QuoteDiagnosisPanel } from "./QuoteDiagnosisPanel";
import type { CoverageView, RollupView } from "./QuoteDiagnosisPanel";
import type { OperationsDiagnosisView, OwnerDiagnosisView } from "./DiagnosisCard";
import type { QuoteDiagnosisState } from "../useQuoteDiagnosis";

const REASONS = [{ code: "PRICE_IS_CORRECT", label: "The price is right" }];

/** The sentence `rollup._coverage_basis` writes for this quote. Verbatim —
 *  nothing in the browser words a rule. */
const CHECKED =
  "2 of 2 lines had enough comparable history to judge the price against. "
  + "1 raised something worth reading.";

const COVERAGE: CoverageView = {
  quote_id: "q1",
  renders: true,
  headline: CHECKED,
  figures: [
    { label: "Lines diagnosed", value: "2" },
    { label: "Compared against history", value: "2" },
    { label: "Not compared", value: "0" },
    { label: "Carrying no quoted price", value: "0" },
    { label: "Raised something worth reading", value: "1" },
  ],
};

/** The line the total buries: quoted exactly what this customer always pays, so
 *  the price engine says nothing about it, and bought for nine times that. */
const SINKER =
  "Line L2 loses ₹3,228 at the price quoted: ₹100 per unit against a cost of "
  + "₹907, on 4, a margin of -807.0%.";

const HEALTHY_TOTAL_HIDING_A_LOSS: RollupView = {
  quote_id: "q1",
  renders: true,
  headline: "1 line loses money at the price quoted — ₹3,228 on it. "
            + "The quote's own total does not show it.",
  total_hides_a_loss: true,
  loss_lines: [{ line_id: "L2", product_id: "ITEM-410", sentence: SINKER }],
  figures: [
    { label: "Quote value", value: "₹8,900" },
    { label: "Value with a cost on record", value: "₹8,900" },
    { label: "Gross profit", value: "₹1,562" },
    { label: "Margin", value: "17.6%" },
    { label: "Margin speaks for", value: "100.0%" },
    { label: "Priced lines with no cost", value: "0" },
    { label: "Judged by policy", value: "ci_abc123" },
  ],
  dominant: "No line's margin movement on this quote could be split between "
            + "the price decision and the cost level, so no dominant factor is "
            + "named.",
  note: "1 line loses money at the price quoted and is listed in full: the "
        + "quote's own total does not show it. Margin is total gross profit "
        + "divided by the revenue whose cost is known, never the mean of the "
        + "per-line margins.",
};

const CLEAN: RollupView = {
  ...HEALTHY_TOTAL_HIDING_A_LOSS,
  headline: "This quote comes to ₹8,900 and earns ₹4,790, a margin of 53.8% "
            + "over the 100.0% of it whose cost is on record. No line on it "
            + "loses money at the price quoted.",
  total_hides_a_loss: false,
  loss_lines: [],
  note: "Margin is total gross profit divided by the revenue whose cost is "
        + "known, never the mean of the per-line margins.",
};

/** The healthy line, and the only one that draws a card. */
function earner(over: Partial<OwnerDiagnosisView> = {}): OwnerDiagnosisView {
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
    attribution: { renders: true, headline: "", drivers: [],
                   note: "NO_COST_BASELINE: no purchase was knowable." },
    working_capital: { assessed: false, interrupts: false, renders: true,
                       headline: "", figures: [], severity: "",
                       strength_word: "",
                       note: "NO_RATE: no cost of capital is set." },
    qualification: "Historical prices may include exceptional deals.",
    actions: ["REVIEW_PRICE"],
    ...over,
  };
}

/** The loss-making line. `renders: false` — the engine found nothing to say
 *  about its price, which is the correct answer and the whole problem. */
const BURIED: OwnerDiagnosisView = earner({
  line_id: "L2",
  renders: false,
  headline: "Consistent with this customer's historical pricing",
  codes: ["WITHIN_HISTORICAL_RANGE"],
});

function deskLine(over: Partial<OperationsDiagnosisView> = {}): OperationsDiagnosisView {
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
    ...over,
  };
}

function state(over: Partial<QuoteDiagnosisState> = {}): QuoteDiagnosisState {
  return { byLineId: {}, coverage: null, rollup: null,
           loading: false, error: null, ...over };
}

function ownerState(rollup: RollupView): QuoteDiagnosisState {
  return state({
    byLineId: { L1: earner(), L2: BURIED },
    coverage: COVERAGE,
    rollup,
  });
}

describe("a line that loses money on a quote whose total is healthy", () => {
  it("is on the owner's screen, although its own line drew no card", () => {
    render(<QuoteDiagnosisPanel
      lineIds={["L1", "L2"]}
      diagnosis={ownerState(HEALTHY_TOTAL_HIDING_A_LOSS)}
      dismissReasons={REASONS}
    />);

    // The card the engine did draw is about the other line entirely.
    expect(screen.getByText("Below this customer's historical pricing")).toBeTruthy();
    expect(screen.queryByText(
      "Consistent with this customer's historical pricing")).toBeNull();

    // And the line nobody was interrupted about is named anyway, with what it
    // loses, in the server's own words.
    expect(screen.getByText(SINKER)).toBeTruthy();
    expect(screen.getByText(HEALTHY_TOTAL_HIDING_A_LOSS.headline)).toBeTruthy();
  });

  it("is an alert rather than a line of prose, because it is one", () => {
    // Scoped by content rather than by count: the cards under it carry their
    // own alerts — each block on a card says in words when it could not read
    // something, which is the rule this one is an instance of.
    render(<QuoteDiagnosisPanel
      lineIds={["L1", "L2"]}
      diagnosis={ownerState(HEALTHY_TOTAL_HIDING_A_LOSS)}
      dismissReasons={REASONS}
    />);

    const alerts = screen.getAllByRole("alert");
    expect(alerts.some((a) => (a.textContent ?? "").includes(SINKER))).toBe(true);
  });

  it("is drawn even when no line on the quote drew a card at all", () => {
    // The hardest version of the same failure: nothing surfaced, so a panel
    // that only drew cards would be empty, and the quote is losing money.
    render(<QuoteDiagnosisPanel
      lineIds={["L1", "L2"]}
      diagnosis={state({
        byLineId: { L1: earner({ renders: false }), L2: BURIED },
        coverage: COVERAGE,
        rollup: HEALTHY_TOTAL_HIDING_A_LOSS,
      })}
      dismissReasons={REASONS}
    />);

    expect(screen.getByText(SINKER)).toBeTruthy();
  });

  it("says the total does not show it, in the server's sentence", () => {
    render(<QuoteDiagnosisPanel
      lineIds={["L1", "L2"]}
      diagnosis={ownerState(HEALTHY_TOTAL_HIDING_A_LOSS)}
      dismissReasons={REASONS}
    />);

    expect(screen.getByText(/The quote's own total does not show it\./))
      .toBeTruthy();
  });
});

describe("a quote with no loss on it", () => {
  it("says so without an alert, and still prints the totals", () => {
    render(<QuoteDiagnosisPanel
      lineIds={["L1", "L2"]}
      diagnosis={ownerState(CLEAN)}
      dismissReasons={REASONS}
    />);

    expect(screen.getByText(CLEAN.headline)).toBeTruthy();
    // No alert on this panel says anything about money being lost. The cards
    // keep their own, which are about what they could not read.
    for (const alert of screen.getAllByRole("alert")) {
      expect(alert.textContent ?? "").not.toMatch(/loses/);
    }
    expect(screen.getByText("Gross profit")).toBeTruthy();
    expect(screen.getByText("₹1,562")).toBeTruthy();
  });

  it("never re-decides whether the total hides one", () => {
    // The flag is the server's. A panel that counted `loss_lines` itself would
    // be a predicate re-derived downstream from published fields, which is a
    // guess about what the producer meant.
    render(<QuoteDiagnosisPanel
      lineIds={["L1"]}
      diagnosis={ownerState({ ...CLEAN, total_hides_a_loss: true })}
      dismissReasons={REASONS}
    />);

    // No loss lines, so no alert, whatever the flag says — the list is the
    // guarantee and the flag is the sentence a screen leads with.
    expect(screen.queryByText(SINKER)).toBeNull();
  });
});

describe("what was checked", () => {
  it("is said on an ordinary quote where nothing was flagged at all", () => {
    render(<QuoteDiagnosisPanel
      lineIds={["L1", "L2"]}
      diagnosis={state({
        byLineId: { L1: earner({ renders: false }), L2: BURIED },
        coverage: COVERAGE,
      })}
      dismissReasons={REASONS}
    />);

    expect(screen.getByText(CHECKED)).toBeTruthy();
  });

  it("yields to a caller who knows something the engine cannot", () => {
    // The ERP quote page counts lines that were never put to the engine — no
    // item code, or no price — and no roll-up over diagnosed lines can see
    // those. Its sentence wins; the panel does not print both.
    render(<QuoteDiagnosisPanel
      lineIds={["L1"]}
      diagnosis={state({ byLineId: { L1: earner() }, coverage: COVERAGE })}
      dismissReasons={REASONS}
      coverage="Two more lines carried no item code, so they were not checked."
    />);

    expect(screen.getByText(
      "Two more lines carried no item code, so they were not checked."))
      .toBeTruthy();
    expect(screen.queryByText(CHECKED)).toBeNull();
  });

  it("is absent only when nothing was asked, which is not the same answer", () => {
    // An unsaved quote, or one with no priced line on it. The panel draws
    // nothing — and the state says why, which is what stops "not asked" being
    // rendered as "nothing was wrong".
    const { container } = render(<QuoteDiagnosisPanel
      lineIds={[]}
      diagnosis={state()}
      dismissReasons={REASONS}
    />);

    expect(container.textContent).toBe("");
  });
});

describe("the desk's panel", () => {
  it("carries no roll-up at all, because the response has no such key", () => {
    const { container } = render(<QuoteDiagnosisPanel
      lineIds={["L1"]}
      diagnosis={state({ byLineId: { L1: deskLine() }, coverage: COVERAGE })}
      dismissReasons={REASONS}
    />);

    // What was checked, and nothing about what anything is worth.
    expect(screen.getByText(CHECKED)).toBeTruthy();
    for (const word of ["₹8,900", "Gross profit", "Margin", "loses"]) {
      expect(container.textContent).not.toContain(word);
    }
  });
});
