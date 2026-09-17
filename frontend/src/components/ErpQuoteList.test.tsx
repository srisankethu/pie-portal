// The list of quotes the ERP raised.
//
// The case this file exists for is the one that caused the report: **a quote
// the ERP has already decided appears**. The server-side half is pinned in
// `tests/decision_platform/test_quote_book.py`; what is pinned here is that the
// screen renders what it is handed, and that the two values a list over money
// must never invent — a missing total, and an outcome nobody recorded — are not
// invented on the way to the DOM.
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ErpQuoteList } from "./ErpQuoteList";
import type { ErpQuote } from "../types";
import { pretendViewportIs } from "../test/viewport";

// ag-grid virtualises its cells and jsdom gives it no layout to do that in, so
// the wide grid renders no row text at all. Every assertion about a *row* below
// therefore goes through the narrow card path, which is plain React — the same
// arrangement `LineGrid.test.tsx` uses and for the same reason. The empty state
// is the exception: `DataGrid` returns it in place of the grid, so it renders
// either way.
function renderRows(quotes: ErpQuote[]) {
  pretendViewportIs(412);
  return render(<ErpQuoteList quotes={quotes} emptyReason={null} />);
}

afterEach(() => { vi.unstubAllGlobals(); });

function q(over: Partial<ErpQuote> = {}): ErpQuote {
  return {
    quote_document_ref: "est-1",
    number: "SLS/QTN-201",
    customer_id: "c1",
    customer_label: "Acme Engineering",
    source_status: "sent",
    outcome: "UNRECORDED",
    raised_on: "2026-05-01",
    expires_on: "2026-06-01",
    decided_on: null,
    value: 10000,
    opened_at: null,
    ...over,
  };
}

describe("a decided quote", () => {
  it("is on the list, which is the whole reason this screen exists", () => {
    renderRows([
      q({ quote_document_ref: "w", number: "QTN-1", outcome: "WON",
          source_status: "accepted", decided_on: "2026-05-20" }),
      q({ quote_document_ref: "l", number: "QTN-2", outcome: "LOST",
          source_status: "declined", decided_on: "2026-05-21" }),
    ]);

    expect(screen.getByText("QTN-1")).toBeTruthy();
    expect(screen.getByText("QTN-2")).toBeTruthy();
    expect(screen.getByText("Won")).toBeTruthy();
    expect(screen.getByText("Lost")).toBeTruthy();
  });
});

describe("an unrecorded outcome", () => {
  it("reads as an absence and never as a loss", () => {
    // The classifier goes out of its way not to call silence a loss. A screen
    // that then prints "Lost" — or colours it like one — throws that away.
    renderRows([q({ outcome: "UNRECORDED" })]);

    expect(screen.getByText("No outcome")).toBeTruthy();
    expect(screen.queryByText("Lost")).toBeNull();
  });

  it("still shows the ERP's own word for it", () => {
    // "expired" and "sent" both classify UNRECORDED, and a reader asking why
    // needs to be able to tell them apart.
    renderRows([q({ source_status: "expired" })]);

    expect(screen.getByText(/expired/)).toBeTruthy();
  });
});

describe("a quote the ERP gave no total for", () => {
  it("is listed, and is not rendered as zero", () => {
    renderRows([q({ quote_document_ref: "novalue", number: "QTN-9", value: null })]);

    expect(screen.getByText("QTN-9")).toBeTruthy();
    const shown = document.body.textContent ?? "";
    expect(shown).not.toMatch(/₹0(?!\d)/);
  });
});

describe("an empty book", () => {
  it("shows the server's reason rather than a generic blank", () => {
    // A bare "Nothing here" is exactly what sent the original report: it cannot
    // distinguish "no quotes synced" from "the quote stage was refused a
    // permission", and those need different actions.
    render(<ErpQuoteList quotes={[]} emptyReason={
      "No quotes have reached the platform from your ERP yet. Reading them "
      + "needs a permission older connections were never asked for."} />);

    expect(screen.getByText(/needs a permission/)).toBeTruthy();
  });

  it("falls back to a plain sentence when the server gave no reason", () => {
    render(<ErpQuoteList quotes={[]} emptyReason={null} />);

    expect(screen.getByText(/Nothing has come through/)).toBeTruthy();
  });
});
