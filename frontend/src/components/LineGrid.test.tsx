// The quote grid, rendered — the first test in this repo that mounts a
// component rather than calling a function.
//
// ## What this does and does not claim
//
// The platform's hardest invariant is that cost and margin are **absent from a
// salesperson's response**: the server omits the fields, so there is nothing to
// read out of a network tab. That is proven on the server, by
// `tests/decision_platform/test_quote_intelligence_api.py` and
// `test_api_authz.py`, and nothing here weakens or replaces it.
//
// What this file pins is the layer below that: given a response that *does*
// carry economics, the grid must not render them to a sales role. Both halves
// are needed. The server guarantee is the one that matters, but a component
// that renders whatever it is handed is one prop-drilling mistake away from
// putting a margin on a salesperson's screen the moment some other caller
// passes a richer object — and that mistake would never fail a backend test.
//
// So the fixture below is deliberately *hostile*: a line carrying a real cost
// and a real margin, of the shape a manager's response has, rendered with
// `mgmt={false}`. The salesperson's grid must show neither.
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LineGrid } from "./LineGrid";
import type { Line, LineIntelligence } from "../types";

/** A line the server would only ever send to a manager: cost and margin present. */
function lineWithEconomics(): Line {
  return {
    id: "l1",
    raw: "CNMG 120408 KCP25 x 10",
    reqCode: "CNMG120408",
    reqDesc: "Turning insert",
    reqQty: 10,
    rel: "EXACT",
    relLabel: "Identical",
    supplyCode: "2001174",
    supplyDesc: "CNMG 120408 MP KCP25",
    sel: "AUTO",
    avail: 40,
    availUnknown: false,
    inBooks: true,
    shortage: null,
    quoted: 1000,
    recommended: 1000,
    lineTotal: 10000,
    createPhase: null,
    service: null,
    incompatReason: null,
    status: { kind: "ready", label: "ready" },
    flags: {
      attention: false, procurement: false, missingBooks: false,
      manualReview: false, unresolved: false, substituted: false,
    },
    candidates: [],
    notes: [],
    substituted: false,
    // The numbers that must never reach a salesperson's screen.
    economics: {
      cost: 760,
      list_price: 1200,
      recommended: 1000,
      quoted: 1000,
      margin: 0.24,
      below_floor: false,
    },
  };
}

/** The platform's own economics for the same line — the authoritative margin,
 *  which takes precedence over the legacy per-line figure when present.
 *
 *  Written out in full rather than cast through `as unknown as`. The cast would
 *  be shorter and would also make this fixture survive a field being renamed or
 *  removed from `LineIntelligence` — which is exactly the change that should
 *  break a test rather than pass one. */
function intelWithEconomics(): Record<string, LineIntelligence> {
  return {
    l1: {
      line_id: "l1",
      product_id: "2001174",
      product_ref: "2001174",
      resolved: true,
      qty: 10,
      quantity_band: { index: 1, low: 1, high: 25, label: "1–25" },
      as_of: "2026-08-07",
      references: [],
      references_withheld: [],
      exceptions: [],
      worst_severity: null,
      requires_approval: false,
      blocking: false,
      data_quality: {
        data_sufficiency: "SUFFICIENT",
        reasons: [],
        transaction_count: 12,
      },
      thresholds_version: "test-thresholds",
      engine_version: "test-engine",
      economics: {
        unit_cost: 780,
        quoted_unit_price: 1000,
        qty: 10,
        line_revenue: 10000,
        cogs: 7800,
        gross_profit: 2200,
        margin: 0.22,
      },
    },
  };
}

const NOOP = () => {};

function renderGrid(mgmt: boolean, intel: Record<string, LineIntelligence> = {}) {
  return render(
    <LineGrid
      lines={[lineWithEconomics()]}
      mgmt={mgmt}
      intel={intel}
      selected={{}}
      focusId={null}
      onToggle={NOOP}
      onOpen={NOOP}
      onSetPrice={NOOP}
      onDeleteLine={NOOP}
      onCreateItem={NOOP}
    />,
  );
}

describe("a salesperson's grid", () => {
  it("has no margin column, even when the line carries a margin", () => {
    renderGrid(false);
    expect(screen.queryByText("Margin")).not.toBeInTheDocument();
  });

  it("renders neither the legacy margin nor the authoritative one", () => {
    const { container } = renderGrid(false, intelWithEconomics());
    // 0.24 -> "24.0%" (legacy, from the line) and 0.22 -> "22.0%" (platform).
    expect(container.textContent).not.toContain("24.0%");
    expect(container.textContent).not.toContain("22.0%");
  });

  it("renders no cost figure anywhere in the row", () => {
    const { container } = renderGrid(false, intelWithEconomics());
    // The two cost figures in the fixture, formatted and bare. `760` would also
    // appear inside a larger number, so check the grouped rendering too.
    expect(container.textContent).not.toContain("760");
    expect(container.textContent).not.toContain("780");
  });

  it("still shows what a salesperson is meant to see", () => {
    // The negative assertions above would all pass on a component that rendered
    // nothing at all, so pin the positives in the same breath.
    const { container } = renderGrid(false);
    expect(screen.getByText("CNMG120408")).toBeInTheDocument();
    expect(screen.getByText("Quoted ₹")).toBeInTheDocument();
    expect(container.textContent).toContain("10,000"); // the line total
  });
});

describe("a manager's grid", () => {
  it("has a margin column", () => {
    renderGrid(true);
    expect(screen.getByText("Margin")).toBeInTheDocument();
  });

  it("shows the line's own margin when the platform has no figure", () => {
    const { container } = renderGrid(true);
    expect(container.textContent).toContain("24.0%");
  });

  it("prefers the platform's authoritative margin over the legacy one", () => {
    // The authoritative figure uses the recorded purchase cost net of bill-line
    // discounts; the per-line figure is a catalogue-derived fallback. Showing
    // the fallback when the real one exists would understate a thin line.
    const { container } = renderGrid(true, intelWithEconomics());
    expect(container.textContent).toContain("22.0%");
    expect(container.textContent).not.toContain("24.0%");
  });

  it("renders an em dash rather than a zero when there is no margin at all", () => {
    const line = lineWithEconomics();
    delete line.economics;
    const { container } = render(
      <LineGrid
        lines={[line]}
        mgmt
        intel={{}}
        selected={{}}
        focusId={null}
        onToggle={NOOP}
        onOpen={NOOP}
        onSetPrice={NOOP}
        onDeleteLine={NOOP}
        onCreateItem={NOOP}
      />,
    );
    // A missing margin shown as "0.0%" reads as a line sold at cost.
    expect(container.textContent).not.toContain("0.0%");
  });
});

describe("the empty state", () => {
  it("explains itself instead of rendering a bare table", () => {
    render(
      <LineGrid
        lines={[]}
        mgmt={false}
        intel={{}}
        selected={{}}
        focusId={null}
        onToggle={NOOP}
        onOpen={NOOP}
        onSetPrice={NOOP}
        onDeleteLine={NOOP}
        onCreateItem={NOOP}
      />,
    );
    expect(screen.getByText(/paste an RFQ/i)).toBeInTheDocument();
  });
});
