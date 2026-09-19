// What the cash on a quote line costs, on the owner's card.
//
// Three things are pinned here, and they fail in three different ways.
//
// **The leak is a type error.** `capital_per_unit` *is* the purchase cost —
// this is the most directly cost-revealing block the diagnosis carries — so the
// key is declared on `OwnerDiagnosisView` and on nothing else. Two of the
// assertions below are `@ts-expect-error`s: they pass by failing to compile,
// which is the only assertion shape that can speak about a property a runtime
// test would have to construct in order to look for.
//
// **A refusal is content, and it names a Settings field.** The rate is
// owner-set with no default, so a book nobody has configured gets `NO_RATE` —
// and the fix is one person typing one number. That sentence has to be on the
// screen. A block that drew nothing would read as "this line ties up no cash",
// which is never true of a line.
//
// **Nothing is formatted here.** Every figure arrives as a string the server
// wrote. The last test sweeps the rendered document for currency figures, day
// counts and percentages and asserts the set is exactly what the payload
// carried — so a component that started doing arithmetic fails rather than
// agreeing by coincidence.
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { OwnerDiagnosisCard } from "./DiagnosisCard";
import type {
  AttributionView, DiagnosisView, OperationsDiagnosisView, OwnerDiagnosisView,
  WorkingCapitalView,
} from "./DiagnosisCard";

const REASONS = [{ code: "PRICE_IS_CORRECT", label: "The price is right" }];

/** Every figure a string the server already wrote — money, days and a rate. */
function assessed(over: Partial<WorkingCapitalView> = {}): WorkingCapitalView {
  return {
    assessed: true,
    interrupts: true,
    renders: true,
    headline: "Funding this line's cash costs ₹49 (₹5 per unit), taking "
              + "0.49 pp off its margin.",
    figures: [
      { label: "Customer pays in", value: "70 days" },
      { label: "Supplier credit", value: "30 days" },
      { label: "Money out for", value: "40 days" },
      { label: "Capital at risk", value: "₹6,000 (₹600 per unit)" },
      { label: "Funding charge", value: "₹49 (₹5 per unit)" },
      { label: "Margin effect", value: "-0.49 pp" },
      { label: "Cost of capital", value: "12.00% a year" },
    ],
    severity: "MAJOR",
    strength_word: "Strong",
    note: "Money is out for 40 days on this line: 70 days from invoice to the "
          + "customer's money arriving, less 30 days of supplier credit.",
    ...over,
  };
}

/** The state a book that has not set a cost of capital is in — which is most of
 *  them, which is why it is the case this block is written around. */
const NO_RATE: WorkingCapitalView = {
  assessed: false,
  interrupts: false,
  renders: true,
  headline: "",
  figures: [],
  severity: "",
  strength_word: "",
  note: "NO_RATE: No annual cost of capital is set, so there is no rate at "
        + "which to charge the money this line ties up. Set 'Annual cost of "
        + "capital' in Settings — what a rupee actually costs this business to "
        + "fund for a year.",
};

const ATTRIBUTION: AttributionView = {
  renders: true, headline: "", drivers: [],
  note: "NO_COST_BASELINE: no purchase was knowable.",
};

function ownerView(over: Partial<OwnerDiagnosisView> = {}): OwnerDiagnosisView {
  return {
    view: "OWNER",
    quote_diagnosis_id: "qd_1",
    line_id: "L1",
    renders: true,
    comparable: true,
    // Deliberately a word no figure below uses, so a query for the reading's
    // confidence cannot match the shell's evidence chip by accident.
    strength_word: "Not enough",
    context: [],
    headline: "Above this customer's historical pricing",
    lines: ["Quoted ₹1,000 per unit against a supported range of ₹1,000."],
    opportunity: "No opportunity on this line.",
    evidence: "10 usable, 0 excluded.",
    codes: ["WITHIN_HISTORICAL_RANGE"],
    qualification: "Historical prices may include exceptional deals.",
    actions: ["REVIEW_PRICE"],
    attribution: ATTRIBUTION,
    working_capital: assessed(),
    ...over,
  };
}

function draw(over: Partial<OwnerDiagnosisView> = {}) {
  return render(
    <OwnerDiagnosisCard diagnosis={ownerView(over)} reasons={REASONS} />);
}

describe("what the cash on this line costs", () => {
  it("is on the owner's card and is declared nowhere else", () => {
    // The runtime half.
    draw();
    expect(
      screen.getByText("What the cash on this line costs", { selector: "*" }),
    ).toBeTruthy();

    // The compile-time half, which is the one that matters. A salesperson's
    // projection has no field to put this in, so reaching it is not a runtime
    // check that could be forgotten — it is a build failure.
    const desk: OperationsDiagnosisView = {
      view: "OPERATIONS",
      quote_diagnosis_id: null,
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
      // @ts-expect-error — an operations projection has no working capital.
      working_capital: assessed(),
    };
    expect(desk.view).toBe("OPERATIONS");

    const reach = (d: DiagnosisView) =>
      // @ts-expect-error — and reaching it without narrowing to OWNER does not
      // compile. Declaring `working_capital?: never` on the operations half
      // would have put the property on the union and lost exactly this.
      d.working_capital;
    expect(typeof reach).toBe("function");
  });

  it("prints every figure the server sent, verbatim", () => {
    draw();

    const capital = assessed();
    for (const figure of capital.figures) {
      expect(screen.getByText(figure.label)).toBeTruthy();
      expect(screen.getByText(figure.value)).toBeTruthy();
    }
    expect(screen.getByText(capital.headline)).toBeTruthy();
    expect(screen.getByText(capital.note)).toBeTruthy();
  });

  it("formats nothing of its own", () => {
    // The sweep the attribution block is held by, pointed at this one: collect
    // every currency figure, day count and percentage in the rendered document
    // and assert the set is exactly what the payload carried. A component that
    // rounded, converted or totalled anything would put a figure in the
    // document that was never in the props.
    const { container } = draw();
    const capital = assessed();

    const pattern = /₹[\d,]+|\d+(?:\.\d+)?\s*(?:pp|days|day|%)/g;
    const shown = new Set(container.textContent?.match(pattern) ?? []);
    const sent = new Set(
      [capital.headline, capital.note,
       ...capital.figures.map((f) => f.value),
       ...ownerView().lines, ownerView().opportunity]
        .join(" ").match(pattern) ?? []);

    for (const figure of shown) {
      expect(sent.has(figure), `${figure} was not in the payload`).toBe(true);
    }
    // And the sweep found something, or it proves nothing.
    expect(shown.size).toBeGreaterThan(3);
  });

  it("says which Settings field finishes it rather than drawing nothing", () => {
    draw({ working_capital: NO_RATE });

    // The refusal is on the screen as an alert — a thing, not the absence of
    // one. An empty panel would read as "nothing to report", which is exactly
    // what an unconfigured rate does not mean.
    const alert = screen.getByText(/No annual cost of capital is set/);
    expect(alert).toBeTruthy();
    expect(alert.textContent).toContain("'Annual cost of capital' in Settings");
    // And no figure is invented to fill the gap.
    expect(screen.queryByText("Capital at risk")).toBeNull();
  });

  it("shows the figures whether or not the reading interrupts anybody", () => {
    // `interrupts` is the surfacing gate and it decides the register, never
    // whether a number is shown. A card somebody opened is not an interruption,
    // and a MINOR reading still belongs on it — which is what the engine's own
    // `_surfaces` says in as many words.
    const quiet = draw({
      working_capital: assessed({ interrupts: false, severity: "MINOR" }),
    });

    expect(within(quiet.container).getByText("Capital at risk")).toBeTruthy();
    expect(
      within(quiet.container).getByText(assessed().headline),
    ).toBeTruthy();
    // The quiet form is ordinary text, not an alert.
    const quietAlerts = within(quiet.container).getAllByRole("alert");
    expect(quietAlerts.some(
      (a) => a.textContent?.includes("Funding this line's cash costs"))).toBe(false);

    // Loud: the same sentence, now something that interrupts.
    quiet.unmount();
    const loud = draw();
    const loudAlerts = within(loud.container).getAllByRole("alert");
    expect(loudAlerts.some(
      (a) => a.textContent?.includes("Funding this line's cash costs"))).toBe(true);
  });

  it("treats a zero charge as a reading rather than a refusal", () => {
    // A supplier whose credit covers the wait charges this line nothing. The
    // component reads the server's `assessed` flag rather than asking whether a
    // figure is present, so this stays a reading with figures attached — the
    // `_identity_candidate` lesson, in a browser.
    draw({
      working_capital: assessed({
        headline: "No funding charge on this line: the money is back before "
                  + "the supplier is paid.",
        interrupts: false,
        severity: "NEGLIGIBLE",
        figures: [
          { label: "Money out for",
            value: "nothing — supplier credit runs 30 days longer" },
          { label: "Funding charge", value: "₹0 (₹0 per unit)" },
        ],
      }),
    });

    expect(screen.getByText(/No funding charge on this line/)).toBeTruthy();
    expect(
      screen.getByText("nothing — supplier credit runs 30 days longer"),
    ).toBeTruthy();
  });

  it("says nothing was read when the block arrives empty", () => {
    // A payload served before this key existed. "Nothing was read" is the true
    // thing to say about it, and it is the same answer the refusal branch gives
    // — never a blank where a figure would be.
    draw({ working_capital: undefined as unknown as WorkingCapitalView });

    expect(screen.getByText(/was not read, and no reason was given/)).toBeTruthy();
  });
});
