// One ERP quote, opened.
//
// The ask this came from was "I am unable to open the Quote, and these should
// be read-only for anyone". So the two things pinned hardest here are that it
// **opens** and that it offers **nothing to press** — no save, no edit, no
// send — for any role, because the role that could act on it does not exist:
// `upsert_quote_document` rewrites every column from the payload on every sync,
// so anything typed here would live until the next pull.
//
// The third is the one a test is least likely to be written for and the one
// this screen exists because of: the panel has to **say what it does not
// hold**. `erp_quotes` is header grain, the lines were never fetched, and a
// reader who opens a quote and finds no mention of them meets the same silence
// that produced the original report.
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ErpQuoteDrawer } from "./ErpQuoteDrawer";
import type { ErpQuote, ErpQuoteLines } from "../types";

function q(over: Partial<ErpQuote> = {}): ErpQuote {
  return {
    quote_document_ref: "est-1",
    number: "QT FY27-018",
    customer_id: "c1",
    customer_label: "M/s. PITTI ENGINEERING LIMITED",
    source_status: "accepted",
    outcome: "WON",
    raised_on: "2026-09-03",
    expires_on: "2026-09-30",
    decided_on: "2026-09-12",
    value: 312043,
    opened_at: null,
    company: "SLS Engineers",
    attributes: {},
    ...over,
  };
}

describe("opening a quote", () => {
  it("shows the header the platform holds", () => {
    render(<ErpQuoteDrawer quote={q()} showCompany={false} onClose={vi.fn()} />);

    expect(screen.getByText("QT FY27-018")).toBeTruthy();
    expect(screen.getByText("M/s. PITTI ENGINEERING LIMITED")).toBeTruthy();
    expect(screen.getByText("Won")).toBeTruthy();
    expect(screen.getByText(/accepted/)).toBeTruthy();
    expect(screen.getByText("2026-09-12")).toBeTruthy();
  });

  it("closes on the close control", () => {
    const onClose = vi.fn();
    render(<ErpQuoteDrawer quote={q()} showCompany={false} onClose={onClose} />);

    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalled();
  });
});

describe("read-only, for anyone", () => {
  it("offers nothing to press but the close control", () => {
    // Not "hides the edit button from a salesperson" — there is no edit button,
    // for any role. A change typed here would be overwritten by the next sync,
    // so the panel must not invite one.
    render(<ErpQuoteDrawer quote={q()} showCompany onClose={vi.fn()} />);

    const pressable = screen.getAllByRole("button").map((b) => b.getAttribute("aria-label")
      ?? b.textContent ?? "");
    expect(pressable).toEqual(["Close"]);
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("says so in words as well", () => {
    render(<ErpQuoteDrawer quote={q()} showCompany={false} onClose={vi.fn()} />);

    // Read off the whole panel: the sentence sits in a node the quote number
    // is interpolated into, so it is not one text node to match against.
    const shown = document.body.textContent ?? "";
    expect(shown).toMatch(/Nothing on this panel can be edited/);
    expect(shown).toMatch(/overwritten by the next sync/);
  });
});

describe("the lines", () => {
  function withLines(lines: ErpQuoteLines["lines"]): ErpQuoteLines {
    return { quote_document_ref: "est-1", lines, lines_held: lines.length > 0,
             empty_reason: null };
  }

  it("are shown, which is the whole point of opening a quote", () => {
    render(<ErpQuoteDrawer quote={q()} showCompany={false} onClose={vi.fn()}
                           lines={withLines([{
                             line_number: 0, item_code: "CNMG120408",
                             description: "CNMG 120408 MP KCP25 turning insert",
                             product_id: "p1", qty: 10, unit: "pcs",
                             rate: 450, amount: 4500,
                           }])} />);

    expect(screen.getByText("CNMG120408")).toBeTruthy();
    expect(screen.getByText(/turning insert/)).toBeTruthy();
    expect(screen.getByText("10 pcs")).toBeTruthy();
    expect(screen.getByText("₹4,500")).toBeTruthy();
  });

  it("show a dash for a line the ERP never priced, not a zero", () => {
    // A line nobody priced is a different fact from a line priced at nothing,
    // and the second is the one a reader would act on.
    render(<ErpQuoteDrawer quote={q()} showCompany={false} onClose={vi.fn()}
                           lines={withLines([{
                             line_number: 0, item_code: "X", description: "",
                             product_id: null, qty: null, unit: "",
                             rate: null, amount: null,
                           }])} />);

    expect(document.body.textContent ?? "").not.toMatch(/₹0(?!\d)/);
  });

  it("are a loading state while the fetch is in flight, not an empty quote", () => {
    // `undefined` is "not fetched yet" and is deliberately distinct from a
    // fetched result with no lines, which is a fact about the quote.
    render(<ErpQuoteDrawer quote={q()} showCompany={false} onClose={vi.fn()} />);

    // `document`, not the render container: a `Drawer` renders through a
    // portal, so its contents are never inside the container it was called on.
    expect(document.querySelector(".MuiSkeleton-root")).toBeTruthy();
  });

  it("carry the server's reason when the breakdown has never been read", () => {
    // The distinction an empty list cannot make on its own: this quote's lines
    // were not pulled, which is not the same as this quote having none. A bare
    // empty table would assert the second.
    render(<ErpQuoteDrawer quote={q()} showCompany={false} onClose={vi.fn()}
                           lines={{
                             quote_document_ref: "est-1", lines: [],
                             lines_held: false,
                             empty_reason: "The lines on this quote have not "
                               + "been read from your ERP yet.",
                           }} />);

    expect(screen.getByText(/have not been read from your ERP yet/)).toBeTruthy();
  });
});

describe("the values a document panel must not invent", () => {
  it("shows a dash for a total the ERP never gave, not a zero", () => {
    render(<ErpQuoteDrawer quote={q({ value: null })} showCompany={false}
                           onClose={vi.fn()} />);

    const shown = document.body.textContent ?? "";
    expect(shown).not.toMatch(/₹0(?!\d)/);
  });

  it("shows a dash for an expiry the ERP never set", () => {
    render(<ErpQuoteDrawer quote={q({ expires_on: null, decided_on: null })}
                           showCompany={false} onClose={vi.fn()} />);

    // Two rows with nothing in them — an absence, not a date invented for the
    // layout's sake.
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(2);
  });
});

describe("the organization's own fields", () => {
  it("are labelled where this file knows the name", () => {
    render(<ErpQuoteDrawer showCompany={false} onClose={vi.fn()}
                           quote={q({ attributes: { cf_quote_type: "Tender" } })} />);

    expect(screen.getByText("Quote type")).toBeTruthy();
    expect(screen.getByText("Tender")).toBeTruthy();
  });

  it("show a key this file has never heard of rather than dropping it", () => {
    // It is a fact somebody typed into their ERP. Hiding it because this file
    // has no label for it would be this screen's own bug, repeated.
    render(<ErpQuoteDrawer showCompany={false} onClose={vi.fn()}
                           quote={q({ attributes: { cf_something_new: "Yes" } })} />);

    expect(screen.getByText("cf_something_new")).toBeTruthy();
    expect(screen.getByText("Yes")).toBeTruthy();
  });

  it("are absent entirely when the ERP set none", () => {
    render(<ErpQuoteDrawer quote={q({ attributes: {} })} showCompany={false}
                           onClose={vi.fn()} />);

    expect(screen.queryByText("Your fields on this quote")).toBeNull();
  });
});

describe("naming the book", () => {
  it("is shown when the organization has more than one", () => {
    render(<ErpQuoteDrawer quote={q()} showCompany onClose={vi.fn()} />);

    expect(screen.getByText("SLS Engineers")).toBeTruthy();
  });

  it("is left off when there is only one, where it would be noise", () => {
    render(<ErpQuoteDrawer quote={q()} showCompany={false} onClose={vi.fn()} />);

    expect(screen.queryByText("SLS Engineers")).toBeNull();
  });
});
