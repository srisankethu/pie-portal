// The engine's caveats must be visible in the case they are about.
//
// The drawer rendered `line.notes` only inside `{line.candidates.length === 0
// && …}` — so a note describing the candidates on screen was shown only when
// there were none. The one that mattered most was the engine's own:
//
//   "All shown candidate(s) matched on family/shape only — no dimension was
//    comparable, so a perfect dimensional score there is vacuous …"
//
// It is stamped precisely when candidates *are* listed, and it was invisible
// every time. A caveat shown only when there is nothing to caveat is not a
// caveat, and the reader was left with a score and no reason to doubt it.
//
// The server-side guards are the ones that matter and are proven in
// `backend/tests/test_pie_service.py` — an unverified candidate is never TECH
// and is never auto-selected. This file pins the half those cannot reach: that
// the person choosing is actually told.
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SupplyDrawer } from "./SupplyDrawer";
import type { Candidate, Line } from "../types";

// Verbatim from `equivalence/query._add_vacuous_match_note` — the point of the
// test is that *this* string reaches the screen, so a paraphrase would pass
// while the real note stayed invisible.
const VACUITY_NOTE =
  "All shown candidate(s) matched on family/shape only — no dimension was " +
  "comparable, so a perfect dimensional score there is vacuous (nothing was " +
  "checked, not a confirmed size match). Confirm dimensions before treating " +
  "one as an equivalent.";

function candidate(over: Partial<Candidate> = {}): Candidate {
  return {
    code: "1855169", desc: "INS. NGE WITH CHIP BREAKER LC R 04",
    rel: "POSSIBLE", grade: null, brand: "Kennametal", score: 1.0,
    reason: "family/shape only", attributes: {}, unverified: true, ...over,
  };
}

/** A line the engine declined to resolve: candidates listed, none selected. */
function lineWithCaveats(over: Partial<Line> = {}): Line {
  return {
    id: "l1", raw: "6205 2RS C3 bearing",
    reqCode: "6205 2RS C3 bearing", reqDesc: "Not resolved — choose the intended product",
    reqQty: 10, rel: "AMBIGUOUS", relLabel: "Ambiguous",
    proposed: false, reading: "",
    supplyCode: null, supplyDesc: "", sel: "AUTO",
    avail: null, availUnknown: true, inBooks: null, shortage: null,
    quoted: null, priceSource: null, recommended: null, lineTotal: null,
    costBasis: null, customCostSet: false,
    createPhase: null, service: null, incompatReason: null,
    status: { kind: "technical", label: "unresolved" },
    flags: {
      attention: true, procurement: false, missingBooks: false,
      manualReview: true, unresolved: true, substituted: false,
    },
    candidates: [candidate(), candidate({ code: "2510324", desc: "ENDMILL 49N9 3FL" })],
    notes: [VACUITY_NOTE],
    substituted: false,
    ...over,
  };
}

function renderDrawer(line: Line) {
  return render(
    <SupplyDrawer
      line={line} customer="Acme" token="t" mgmt={false}
      intel={null} intelLoading={false} intelError={null}
      onRecordOverride={vi.fn()} onRequestApproval={vi.fn()}
      approvalStatus={null} onClose={vi.fn()} onSetCustomCost={vi.fn()}
      onSelect={vi.fn()}
      onRevert={vi.fn()}
    />,
  );
}

describe("SupplyDrawer caveats", () => {
  // The drawer mounts `DecisionSupport`, which asks the platform about this
  // line on mount. Nothing here is about that panel, and an unstubbed fetch
  // resolves after the assertions, updating state outside `act` — a warning
  // that would train a reader to ignore warnings in this file.
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
  });

  it("shows the engine's note when there ARE candidates — the case it is about", () => {
    renderDrawer(lineWithCaveats());

    expect(screen.getByText(VACUITY_NOTE)).toBeInTheDocument();
  });

  it("still shows notes when there are no candidates", () => {
    renderDrawer(lineWithCaveats({ candidates: [], notes: [VACUITY_NOTE] }));

    expect(screen.getByText(VACUITY_NOTE)).toBeInTheDocument();
  });

  it("shows nothing where there is nothing to say", () => {
    // The caveat block must not become furniture that is always on screen —
    // a heading over an empty list teaches people to skip the whole panel.
    renderDrawer(lineWithCaveats({ notes: [] }));

    expect(screen.queryByText(/Before you choose/i)).not.toBeInTheDocument();
  });

  it("labels a retrieved candidate as nearest by description, never as a match", () => {
    // `backend/tests/test_pie_service.py` pins that such a record is POSSIBLE,
    // unscored and never selected; this pins that the person is told which
    // kind of candidate they are looking at.
    renderDrawer(lineWithCaveats({
      candidates: [
        candidate({ code: "2035689", desc: "KSOM INSERT OFPT-ENGB R=1.2", unverified: false }),
        candidate({ code: "5642232", desc: "VSM11 MILLING INSERT R=1.2 MM",
                    score: null, unverified: false, retrieved: true,
                    reason: "Nearest catalogue description to this text (0.71 similar), not a ranked match." }),
      ],
      notes: [],
    }));

    expect(screen.getAllByText("nearest by description")).toHaveLength(1);
    expect(screen.getByText(/not a ranked match/)).toBeTruthy();
  });

  it("names the confirmed code a retrieved candidate was found through", () => {
    renderDrawer(lineWithCaveats({
      candidates: [
        candidate({ code: "2001174", desc: "CNMG 120408-49 - TN2000",
                    score: null, unverified: true, retrieved: true, alias: "PITTI-7781",
                    reason: "Near a code this customer confirmed as this product." }),
      ],
      notes: [],
    }));

    expect(screen.getByText("near confirmed code PITTI-7781")).toBeTruthy();
    expect(screen.queryByText("nearest by description")).not.toBeInTheDocument();
  });

  it("says a past choice is a past choice, not a confirmation", () => {
    renderDrawer(lineWithCaveats({
      candidates: [
        candidate({ code: "4149315", desc: "SC DRILL 12mm/.4724/ 5xD COOLANT",
                    score: null, unverified: true, retrieved: true,
                    alias: "12mm drill for SS", alias_kind: "phrase",
                    reason: "This customer was quoted this product before." }),
      ],
      notes: [],
    }));

    expect(screen.getByText("quoted before for “12mm drill for SS”")).toBeTruthy();
    expect(screen.queryByText(/confirmed code/)).not.toBeInTheDocument();
  });

  it("does not present an unverified candidate as selected supply", () => {
    // The server decides this; the drawer must not contradict it by drawing a
    // selection the response does not carry.
    const { container } = renderDrawer(lineWithCaveats());

    expect(container.querySelector(".cand.selected")).toBeNull();
  });
});

// The source of a candidate, which became load-bearing when this organization's
// own book joined the candidate pool. Before that, `brand` was a manufacturer's
// name and a missing one cost nothing; now it is the difference between "the
// maker lists this" and "we already sell this", and a book item and a catalogue
// item for the same physical product can appear in one list.
describe("SupplyDrawer candidate source", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
  });

  it("marks a candidate that came from this organization's own book", () => {
    renderDrawer(lineWithCaveats({
      candidates: [candidate({ brand: "book", grade: null })],
    }));

    expect(screen.getByText("in our book")).toBeInTheDocument();
    // And in the metadata line as well, not only as a chip — a chip is a
    // second cue, never the only one. Matched exactly, because `/our book/`
    // also matches the chip and would pass on either alone.
    expect(screen.getByText("our book")).toBeInTheDocument();
  });

  it("shows the source even when the grade did not decode", () => {
    // The defect this pins. `brand` was rendered inside the grade's guard, so a
    // candidate with no decoded grade — which a book item routinely is — showed
    // nothing at all about where it came from.
    renderDrawer(lineWithCaveats({
      candidates: [candidate({ brand: "Kennametal", grade: null })],
    }));

    expect(screen.getByText(/Kennametal/)).toBeInTheDocument();
  });

  it("still shows the grade beside the source when both are known", () => {
    renderDrawer(lineWithCaveats({
      candidates: [candidate({ brand: "Kennametal", grade: "TN2000" })],
    }));

    expect(screen.getByText(/grade TN2000 · Kennametal/)).toBeInTheDocument();
  });

  it("does not mark an ordinary catalogue candidate as ours", () => {
    // The negative control. Without it the marker could be drawn on every
    // candidate and the test above would still pass — a mark on everything
    // marks nothing.
    renderDrawer(lineWithCaveats({
      candidates: [candidate({ brand: "Kennametal" })],
    }));

    expect(screen.queryByText("in our book")).not.toBeInTheDocument();
  });

  it("says when the engine could not verify the fit", () => {
    // `unverified` reaches the browser and was rendered nowhere. The reason
    // prose carries it, but a person scanning six candidates reads chips.
    renderDrawer(lineWithCaveats({
      candidates: [candidate({ unverified: true })],
    }));

    expect(screen.getByText("unverified fit")).toBeInTheDocument();
  });

  it("says nothing about verification when the comparison was complete", () => {
    renderDrawer(lineWithCaveats({
      candidates: [candidate({ unverified: false })],
    }));

    expect(screen.queryByText("unverified fit")).not.toBeInTheDocument();
  });
});
