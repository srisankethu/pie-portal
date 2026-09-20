// Driver attribution on the owner's diagnosis card.
//
// Three things are pinned here, and they fail in three different ways.
//
// **The leak is a type error.** Every figure in an attribution is derived from
// purchase cost, so the key is declared on `OwnerDiagnosisView` and on nothing
// else. Two of the assertions below are `@ts-expect-error`s: they pass by
// failing to compile, which is the only assertion shape that can speak about a
// property a runtime test would have to construct in order to look for.
//
// **A refusal is content.** When the engine will not assert a split it says
// what was missing, and that sentence has to be on the screen. A block that
// drew nothing would read as "all clear" — `CLAUDE.md` §1's *absence of
// evidence is not a pass*, which has now been found three times on the server
// and would be just as wrong in a layout.
//
// **Severity and strength are not one scale.** Severity is how much a factor
// moved margin; strength is how much to believe it, and a large effect off a
// thin history is severe and weakly evidenced at once. The test that keeps them
// apart asserts the *shape* — one is a chip, the other is a labelled word —
// because two chips would read as one ramp however the words were chosen.
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { OwnerDiagnosisCard } from "./DiagnosisCard";
import type {
  AttributionView, DiagnosisView, OperationsDiagnosisView, OwnerDiagnosisView,
} from "./DiagnosisCard";

const REASONS = [{ code: "PRICE_IS_CORRECT", label: "The price is right" }];

/** The asserted split. Both effects are strings the server already formatted —
 *  which is the point of the `renders it verbatim` test below. */
function attribution(over: Partial<AttributionView> = {}): AttributionView {
  return {
    renders: true,
    reason: "PRICE_THEN_COST",
    headline: "Margin fell 6.4 points: the price decision owns 4.1 of it and "
              + "the cost level 2.3.",
    drivers: [
      {
        code: "PRICE_POSITION_EFFECT",
        severity: "MAJOR",
        strength_word: "Strong",
        // `render.DriverLine.effect`'s own spelling, money and all. The
        // component prints it; it does not build it.
        effect: "-4.10 pp (-₹21 per unit)",
        basis: "At the cost level history had established, quoting ₹339 "
               + "against a median of ₹360 costs 4.1 points of margin.",
      },
      {
        code: "COST_LEVEL_EFFECT",
        severity: "MINOR",
        strength_word: "Moderate",
        effect: "-2.30 pp (-₹12 per unit)",
        basis: "At the price this line carries, the expected cost of ₹150 "
               + "against a historical ₹142 costs 2.3 points.",
      },
    ],
    note: "",
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
    // Deliberately different from any driver's word, so a query for a driver's
    // strength cannot match the shell's evidence chip by accident.
    strength_word: "Not enough",
    context: [],
    headline: "Above this customer's historical pricing",
    lines: ["Quoted ₹339 per unit against a supported range of ₹218."],
    opportunity: "Roughly ₹6,065 on this line at the top of the band.",
    evidence: "11 usable, 0 excluded.",
    codes: ["ABOVE_HISTORICAL_RANGE"],
    qualification: "Historical prices may include exceptional deals.",
    actions: ["REVIEW_PRICE"],
    attribution: attribution(),
    // Required on this projection and irrelevant to this file: a refusal, so
    // the card draws its block and nothing here has to reason about it.
    // `DiagnosisCard.workingcapital.test.tsx` is where it is pinned.
    working_capital: {
      assessed: false, reason: "NO_RATE", interrupts: false, renders: true,
      headline: "", figures: [], severity: "", strength_word: "",
      note: "No annual cost of capital is set.",
    },
    ...over,
  };
}

function draw(over: Partial<OwnerDiagnosisView> = {}) {
  return render(
    <OwnerDiagnosisCard diagnosis={ownerView(over)} reasons={REASONS} />);
}

describe("the split", () => {
  it("leads with the server's one sentence", () => {
    draw();

    expect(screen.getByText(/the price decision owns 4.1 of it/)).toBeTruthy();
  });

  it("renders each effect verbatim, and computes nothing", () => {
    draw();

    // Byte for byte what the server wrote — sign, figure and unit. Nothing in
    // the component formats a figure or picks a symbol: `CLAUDE.md` §3 puts
    // every number a screen renders in `commercial/`.
    expect(screen.getByText("-4.10 pp (-₹21 per unit)")).toBeTruthy();
    expect(screen.getByText("-2.30 pp (-₹12 per unit)")).toBeTruthy();

    // And no figure the payload did not contain. The card's rupee amounts are
    // exactly the ones the server's own sentences carry.
    const shown = document.body.textContent ?? "";
    const figures = [...shown.matchAll(/₹[\d,]+/g)].map((m) => m[0]);
    expect(new Set(figures)).toEqual(
      new Set(["₹21", "₹12", "₹339", "₹360", "₹150",
               "₹142", "₹218", "₹6,065"]));
  });

  it("names both factors and the counterfactual each one answers", () => {
    draw();

    expect(screen.getByText("PRICE POSITION EFFECT")).toBeTruthy();
    expect(screen.getByText("COST LEVEL EFFECT")).toBeTruthy();
    expect(screen.getByText(/At the cost level history had established/)).toBeTruthy();
    expect(screen.getByText(/At the price this line carries/)).toBeTruthy();
  });
});

describe("severity and strength", () => {
  it("are two scales and cannot be read as one", () => {
    draw();

    // Severity grades the figure and is a chip.
    const severity = screen.getByText("MAJOR");
    expect(severity.closest(".MuiChip-root")).toBeTruthy();

    // Strength grades the evidence, is labelled with the question it answers,
    // and is deliberately not a chip — two chips stepping through the same
    // tones beside each other would be one ramp whatever the words said.
    const strength = screen.getByText(/Confidence in this: Moderate/);
    expect(strength.closest(".MuiChip-root")).toBeNull();
  });

  it("gives a driver row exactly one chip", () => {
    const { container } = draw();

    // Two drivers, two chips, plus the shell's own evidence chip. A third per
    // row would be the collapse this card is built to avoid.
    expect(container.querySelectorAll(".MuiChip-root").length).toBe(3);
  });
});

describe("a refusal", () => {
  it("is content, not an empty block, when the engine will not assert", () => {
    draw({ attribution: attribution({
      renders: false,
      headline: "",
      drivers: [],
      note: "No purchase of this item is knowable as at the quote date, so the "
            + "movement could not be split.",
    }) });

    expect(screen.getByText("What moved the margin")).toBeTruthy();
    expect(screen.getByText(/No purchase of this item is knowable/)).toBeTruthy();
  });

  it("says so too when the drivers are empty but the flag is not", () => {
    // `renders` true with nothing to render is still nothing asserted. Reading
    // only the flag is how a caller gets told an unasserted split held.
    draw({ attribution: attribution({
      drivers: [],
      note: "The effects did not account for the movement, so the split was "
            + "refused.",
    }) });

    expect(screen.getByText(/the split was refused/)).toBeTruthy();
    expect(screen.queryByText("PRICE POSITION EFFECT")).toBeNull();
  });

  it("still says something when even the reason is missing", () => {
    // The payload's own worst case, and the one that would otherwise read as
    // good news: nothing asserted, nothing explained, and a block that drew
    // nothing at all.
    draw({ attribution: { renders: false, reason: "NO_COST_BASELINE",
                          headline: "", drivers: [], note: "" } });

    expect(screen.getByText(/no reason was given/)).toBeTruthy();
  });
});

describe("the operations projection", () => {
  it("cannot be built carrying an attribution", () => {
    const base: OperationsDiagnosisView = {
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
      evidence_detail: "14 comparable transactions",
      why: "This customer has purchased this item 14 times.",
      note: "",
      qualification: "Historical prices may include exceptional deals.",
      actions: ["REVIEW_PRICE"],
    };

    const leaked: OperationsDiagnosisView = {
      ...base,
      // @ts-expect-error — `attribution` is declared on the OWNER member only,
      // and every figure in it is derived from purchase cost.
      attribution: attribution(),
    };

    expect(leaked.view).toBe("OPERATIONS");
  });

  it("cannot have one read off it through the union", () => {
    const reach = (d: DiagnosisView) =>
      // @ts-expect-error — reaching it without narrowing to OWNER does not
      // compile. Declaring `attribution?: never` on the operations half would
      // have put the property on the union and lost exactly this.
      d.attribution;

    expect(reach(ownerView())).toBeTruthy();
  });
});
