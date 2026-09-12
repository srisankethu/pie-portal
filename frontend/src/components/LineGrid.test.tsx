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
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LineGrid } from "./LineGrid";
import { NARROW_BREAKPOINT } from "../platform/DataGrid";
import type { Line, LineIntelligence } from "../types";
import type { Fix } from "./lineProblems";

/** A line the server would only ever send to a manager: cost and margin present. */
function lineWithEconomics(): Line {
  return {
    id: "l1",
    raw: "CNMG 120408 KCP25 x 10",
    // Typed by a person, not read from prose — so no confirmation is pending.
    proposed: false,
    reading: "",
    reqCode: "CNMG120408",
    reqDesc: "Turning insert",
    reqQty: 10,
    rel: "EXACT",
    relLabel: "Identical",
    supplyCode: "2001174",
    supplyDesc: "CNMG 120408 MP KCP25",
    costBasis: "BOOKS",
    customCostSet: false,
    sel: "AUTO",
    avail: 40,
    availUnknown: false,
    inBooks: true,
    shortage: null,
    quoted: 1000,
    // A price a person set, so the rate cell carries no "list" mark.
    priceSource: "USER",
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

/** Every prop `LineGrid` needs, with the two that vary named.
 *
 *  Extracted after a `jscpd --min-tokens 30` pass named three copies of the
 *  same ten-prop element in this file. A fixture repeated three times is a
 *  fixture that gets updated twice. */
function props(mgmt: boolean, intel: Record<string, LineIntelligence> = {},
               lines: Line[] = [lineWithEconomics()]) {
  return {
    lines, mgmt, intel,
    // The connected system's own words. Named here rather than defaulted in
    // the component: the whole point of the prop is that the grid never
    // decides what to call somebody's ERP.
    systemShort: "Zoho",
    selectedIds: [] as string[],
    onSelectionChange: NOOP,
    onOpen: NOOP,
    onSetPrice: NOOP,
    onDeleteLine: NOOP,
    onFix: NOOP,
  };
}

/** Render, and wait for the grid to actually exist.
 *
 *  `DataGrid` is a `React.lazy` chunk behind `Suspense` — ag-grid is roughly the
 *  size of the rest of the app, so it is deliberately not in the main bundle.
 *  That makes every assertion here necessarily asynchronous: a synchronous read
 *  of `container.textContent` sees the loading skeleton, which is empty.
 *
 *  This matters for more than convenience. The important assertions below are
 *  *negative* — that a salesperson's grid contains no margin and no cost — and a
 *  negative assertion against an unrendered component passes for the wrong
 *  reason. So every test waits on a column that is always present, and the
 *  helper returns only once the grid is genuinely on screen. If the grid ever
 *  stops rendering, these fail on the wait rather than passing vacuously.
 */
async function renderGrid(mgmt: boolean, intel: Record<string, LineIntelligence> = {}) {
  const view = render(<LineGrid {...props(mgmt, intel)} />);
  // Present for every role, so it proves the grid mounted without asserting
  // anything about what this particular role is allowed to see.
  await screen.findByText("Line total");
  return view;
}

describe("a salesperson's grid", () => {
  it("has no margin column, even when the line carries a margin", async () => {
    await renderGrid(false);
    expect(screen.queryByText("Margin")).not.toBeInTheDocument();
  });

  it("renders neither the legacy margin nor the authoritative one", async () => {
    const { container } = await renderGrid(false, intelWithEconomics());
    // 0.24 -> "24.0%" (legacy, from the line) and 0.22 -> "22.0%" (platform).
    expect(container.textContent).not.toContain("24.0%");
    expect(container.textContent).not.toContain("22.0%");
  });

  it("renders no cost figure anywhere in the grid", async () => {
    const { container } = await renderGrid(false, intelWithEconomics());
    // The two unit costs in the fixture. Checked as bare digits because a cost
    // leaking through an unformatted cell would not carry a currency symbol.
    expect(container.textContent).not.toContain("760");
    expect(container.textContent).not.toContain("780");
  });

  it("still shows what a salesperson is meant to see", async () => {
    // The negatives above would all hold for a component that rendered nothing,
    // and the wait in `renderGrid` is what rules that out. This pins the other
    // half explicitly: the row is really there, with its own figures.
    const { container } = await renderGrid(false);
    expect(screen.getByText("2001174")).toBeInTheDocument();   // the supply item
    expect(screen.getByText("Rate")).toBeInTheDocument();
    expect(container.textContent).toContain("10,000"); // the line total
  });

  it("offers no economics toggle to a role with no economics", async () => {
    // `econ` defaults to on, and the column is still absent: the toggle governs
    // a manager's two columns, never whether a salesperson has them.
    render(<LineGrid {...props(false, intelWithEconomics())} econ />);
    await screen.findAllByText("Line total");
    expect(screen.queryByText("Cost")).not.toBeInTheDocument();
    expect(screen.queryByText("Margin")).not.toBeInTheDocument();
  });
});

describe("a manager's grid", () => {
  it("has a margin column", async () => {
    await renderGrid(true);
    expect(screen.getByText("Margin")).toBeInTheDocument();
  });

  it("puts cost and margin away when the economics are toggled off", async () => {
    // The toggle is what makes five columns the default and seven the pricing
    // view. It hides the columns; it does not change what the server sent.
    render(<LineGrid {...props(true, intelWithEconomics())} econ={false} />);
    await screen.findAllByText("Line total");
    expect(screen.queryByText("Cost")).not.toBeInTheDocument();
    expect(screen.queryByText("Margin")).not.toBeInTheDocument();
  });

  it("shows the line's own margin when the platform has no figure", async () => {
    const { container } = await renderGrid(true);
    expect(container.textContent).toContain("24.0%");
  });

  it("prefers the platform's authoritative margin over the legacy one", async () => {
    // The authoritative figure uses the recorded purchase cost net of bill-line
    // discounts; the per-line figure is a catalogue-derived fallback. Showing
    // the fallback when the real one exists would understate a thin line.
    const { container } = await renderGrid(true, intelWithEconomics());
    expect(container.textContent).toContain("22.0%");
    expect(container.textContent).not.toContain("24.0%");
  });
});

// ── the narrow rendering ─────────────────────────────────────────────────────
//
// jsdom has no `window.matchMedia` at all, which is why every test above
// exercises the grid: MUI's `useMediaQuery` returns its default — false — when
// the API is missing, so the wrapper takes the wide path. Installing a stub is
// therefore not a convenience, it is the only way to reach the other branch.

/** A `matchMedia` that answers one question honestly: is the viewport narrower
 *  than the width the query names? Everything else about it is inert. */
function pretendViewportIs(width: number) {
  vi.stubGlobal("matchMedia", (query: string) => {
    const max = /max-width:\s*([\d.]+)px/.exec(query);
    return {
      matches: max ? width <= Number.parseFloat(max[1]) : false,
      media: query,
      onchange: null,
      addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {},
      dispatchEvent: () => false,
    };
  });
}

afterEach(() => vi.unstubAllGlobals());

/** A phone. 412px is the Pixel 7 the finding was measured on. */
function renderNarrowGrid(mgmt: boolean, intel: Record<string, LineIntelligence> = {}) {
  pretendViewportIs(412);
  return render(<LineGrid {...props(mgmt, intel)} />);
}

describe("on a phone", () => {
  it("puts a rate field on screen for every line", () => {
    // The whole finding, as an assertion. At 412px the grid rendered its
    // "Quoted ₹" column at x=486 in a 412px viewport, with no horizontal
    // scrollbar to say so — five editable cells in the DOM and none of them
    // reachable. There is one field per line here, and it is a real input
    // rather than a cell that becomes one on a second tap.
    renderNarrowGrid(false);
    const price = screen.getByLabelText("Quoted rate for CNMG120408");
    expect(price).toBeInTheDocument();
    expect(price).toHaveValue("1000");
  });

  it("shows what the line is worked on by", () => {
    // Synchronous, unlike every test above: the cards are not behind the lazy
    // ag-grid chunk, which is the other half of what this rendering buys — a
    // phone never downloads the 1.16 MB grid at all.
    const { container } = renderNarrowGrid(false);
    expect(screen.getByText("2001174")).toBeInTheDocument();      // what goes out
    expect(screen.getByText("asked for CNMG120408")).toBeInTheDocument();
    expect(screen.getByText("Qty")).toBeInTheDocument();
    expect(container.textContent).toContain("10,000");            // line total
  });

  it("renders neither cost nor margin for a salesperson", () => {
    // The same hostile fixture as the grid tests, against the second renderer.
    // A card that read `line.economics` directly would put a margin on a
    // salesperson's phone while every assertion above this block still passed.
    const { container } = renderNarrowGrid(false, intelWithEconomics());
    expect(container.textContent).not.toContain("24.0%");
    expect(container.textContent).not.toContain("22.0%");
    expect(container.textContent).not.toContain("760");
    expect(container.textContent).not.toContain("780");
  });

  it("gives a manager the margin, as the grid does", () => {
    const { container } = renderNarrowGrid(true, intelWithEconomics());
    expect(container.textContent).toContain("22.0%");
  });

  it("leaves the grid in place at the width above the breakpoint", () => {
    // The fix is a breakpoint, not a redesign: one pixel wider and this is the
    // grid it has always been. Asserted against the exported constant so the
    // two cannot drift.
    pretendViewportIs(NARROW_BREAKPOINT);
    const { container } = render(<LineGrid {...props(false)} />);
    expect(container.querySelector("[data-line-card]")).toBeNull();
  });
});

// ── whose number is in the rate cell ─────────────────────────────────────────
//
// A resolved line opens at the catalogue rate, which is a useful default and was
// an invisible one: it read exactly like a price somebody had chosen, so a fresh
// RFQ showed a Quotation total nobody had looked at. The mark is what makes the
// default honest, so it is worth a test in both renderings.

/** The same line, still at the rate the catalogue opened it with. */
function atListPrice(): Line {
  return { ...lineWithEconomics(), priceSource: "LIST" };
}

describe("a rate nobody has agreed to", () => {
  it("is marked in the grid, and an agreed one is not", async () => {
    const { container } = await renderGrid(false);
    expect(container.textContent).toContain("1,000");
    expect(container.textContent).not.toContain("list");

    const marked = render(<LineGrid {...props(false, {}, [atListPrice()])} />);
    await screen.findAllByText("Line total");
    expect(marked.container.textContent).toContain("list");
  });

  it("is marked on a phone, where the tooltip cannot be reached", () => {
    pretendViewportIs(412);
    render(<LineGrid {...props(false, {}, [atListPrice()])} />);
    // In the field's own label: the card has no header row to carry it.
    // `getAllBy`, because MUI draws an outlined field's label twice — once as
    // the `<label>` and once in the fieldset's notch.
    expect(screen.getByLabelText("Quoted rate for CNMG120408")).toBeInTheDocument();
    expect(screen.getAllByText("Rate (list)").length).toBeGreaterThan(0);
  });
});

// ── the problem, on the row it is about ─────────────────────────────────────
//
// The point of the redesign: what is wrong with a line, and what fixes it, sit
// under that line rather than in a column of chips and a refusal at Send. Two
// things are worth pinning — that the strip is drawn at all, and that pressing
// its button asks the screen for the specific fix rather than opening something
// for the reader to go and find.

/** A line nothing matched, with the two candidates the engine ranked. */
function unresolved(): Line {
  const l = lineWithEconomics();
  return {
    ...l,
    supplyCode: null, supplyDesc: "",
    status: { kind: "technical", label: "UNRESOLVED" },
    flags: { ...l.flags, unresolved: true, attention: true },
    candidates: [
      { code: "63201", desc: "TiAlN 4FL", rel: "TECH", grade: null, brand: null,
        score: 0.93, reason: "same geometry", attributes: {} },
      { code: "63204", desc: "AlCrN 4FL", rel: "COMPAT", grade: null, brand: null,
        score: 0.81, reason: "compatible", attributes: {} },
    ],
  };
}

describe("a line with a problem", () => {
  it("says so under the row, with the fix on it", async () => {
    render(<LineGrid {...props(false, {}, [unresolved()])} />);
    await screen.findByText("Line total");
    expect(await screen.findByText("Which item is this?")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "63201 · tech" })).toBeInTheDocument();
  });

  it("asks the screen for that fix, naming the line and the candidate", async () => {
    const onFix = vi.fn<(line: Line, fix: Fix) => void>();
    render(<LineGrid {...props(false, {}, [unresolved()])} onFix={onFix} />);
    await screen.findByText("Line total");

    fireEvent.click(await screen.findByRole("button", { name: "63201 · tech" }));
    expect(onFix).toHaveBeenCalledTimes(1);
    const [line, fix] = onFix.mock.calls[0];
    expect(line.id).toBe("l1");
    expect(fix).toEqual({ kind: "choose-candidate", code: "63201" });
  });

  it("draws the same problem on a phone", () => {
    pretendViewportIs(412);
    render(<LineGrid {...props(false, {}, [unresolved()])} />);
    expect(screen.getByText("Which item is this?")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "63201 · tech" })).toBeInTheDocument();
  });
});

describe("the empty state", () => {
  it("explains itself instead of rendering a bare grid", () => {
    render(<LineGrid {...props(false, {}, [])} />);
    expect(screen.getByText(/paste an RFQ/i)).toBeInTheDocument();
  });
});

// ── the ledger has a name, and it is not always Zoho ────────────────────────
//
// The grid printed the word "Zoho" at every customer: the chip on a line the
// ledger does not hold said "no Zoho item" and the button that creates one
// said "+ Zoho". A reader running Business Central was being pointed at a
// system they do not have. The names are props now — the server's words for
// whichever system the quote's books are in (`Quote.systemShort`) — and what
// is pinned here is that the grid *reads them* rather than deciding for
// itself. Both assertions matter: the right name present, and the wrong one
// absent. Only the second one fails when somebody hardcodes it again.
describe("naming the system the books are in", () => {
  function notInBooks(): Line {
    const line = lineWithEconomics();
    return {
      ...line,
      inBooks: false,
      status: { kind: "operational", label: "NOT IN BOOKS" },
      flags: { ...line.flags, attention: true, missingBooks: true },
    };
  }

  it("names the connected system on a line the ledger does not hold", async () => {
    render(<LineGrid {...props(false, {}, [notInBooks()])} systemShort="D365 BC" />);
    await screen.findByText("Line total");
    expect(screen.getByText("not in D365 BC")).toBeInTheDocument();
    expect(screen.queryByText(/zoho/i)).toBeNull();
  });

  it("names it on the strip that offers to create the item", () => {
    pretendViewportIs(412);
    render(<LineGrid {...props(false, {}, [notInBooks()])} systemShort="P21" />);
    expect(screen.getByRole("button", { name: "Create in P21" })).toBeInTheDocument();
    expect(screen.queryByText(/zoho/i)).toBeNull();
  });
});
