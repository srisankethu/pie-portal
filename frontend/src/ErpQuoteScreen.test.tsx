// One ERP quote, as a page.
//
// Three things are pinned hardest, and each of them is a thing that actually
// went wrong:
//
// 1. **It resolves.** The first version sat on a loading skeleton for ever when
//    it could not fetch — the effect set the loading state and returned — and a
//    skeleton that never resolves is indistinguishable from a hang. Every state
//    below asserts the skeleton is gone.
// 2. **The lines are on it.** The whole reason a person opens a quote.
// 3. **Nothing can be edited, by anybody.** Not "the edit button is hidden from
//    a salesperson" — there is no edit control for any role, because a change
//    typed here would be overwritten by the next sync.
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ErpQuoteScreen from "./ErpQuoteScreen";
import type { ErpQuote, ErpQuoteLines } from "./types";
import type { PlatformSession } from "./platform/types";

const listErpQuotes = vi.fn();
const erpQuoteLines = vi.fn();

vi.mock("./api", async () => {
  const actual = await vi.importActual<typeof import("./api")>("./api");
  return {
    ...actual,
    api: {
      ...actual.api,
      listErpQuotes: (...a: unknown[]) => listErpQuotes(...a),
      erpQuoteLines: (...a: unknown[]) => erpQuoteLines(...a),
    },
  };
});

const REF = "2263307000011272461";

function session(): PlatformSession {
  return {
    token: "tok", role: "OWNER", name: "Test", user_id: "u1",
    organization_id: "org1", currency: "INR", timezone: "Asia/Kolkata",
  } as PlatformSession;
}

function quote(over: Partial<ErpQuote> = {}): ErpQuote {
  return {
    quote_document_ref: REF,
    number: "QT FY27-013",
    customer_id: "c1",
    customer_label: "M/s. PITTI ENGINEERING LIMITED",
    source_status: "invoiced",
    outcome: "WON",
    raised_on: "2026-07-23",
    expires_on: null,
    decided_on: "2026-07-24",
    value: 101139,
    opened_at: null,
    company: "SLS Engineers",
    attributes: {},
    ...over,
  };
}

function lines(over: Partial<ErpQuoteLines> = {}): ErpQuoteLines {
  return {
    quote_document_ref: REF,
    lines: [
      { line_number: 0, item_code: "CNMG120408",
        description: "CNMG 120408 MP KCP25 turning insert", product_id: "p1",
        qty: 10, unit: "pcs", rate: 450, amount: 4500 },
      { line_number: 1, item_code: "", description: "Freight",
        product_id: null, qty: 1, unit: "nos", rate: 500, amount: 500 },
    ],
    lines_held: true,
    empty_reason: null,
    ...over,
  };
}

function draw() {
  return render(
    <MemoryRouter initialEntries={[`/quotes/erp/${REF}`]}>
      <Routes>
        <Route path="/quotes/erp/:ref"
               element={<ErpQuoteScreen session={session()} />} />
      </Routes>
    </MemoryRouter>);
}

beforeEach(() => {
  listErpQuotes.mockResolvedValue({ quotes_listed: [quote()] });
  erpQuoteLines.mockResolvedValue(lines());
});
afterEach(() => { listErpQuotes.mockReset(); erpQuoteLines.mockReset(); });

describe("the quote", () => {
  it("shows its header", async () => {
    draw();

    await waitFor(() => expect(screen.getByText("QT FY27-013")).toBeTruthy());
    expect(screen.getByText("M/s. PITTI ENGINEERING LIMITED")).toBeTruthy();
    expect(screen.getByText("Won")).toBeTruthy();
    expect(screen.getByText(/invoiced/)).toBeTruthy();
    expect(screen.getByText("₹1,01,139")).toBeTruthy();
  });

  it("asks for its own reference", async () => {
    draw();

    await waitFor(() => expect(erpQuoteLines).toHaveBeenCalledWith("tok", REF));
  });

  it("shows a dash for a date the ERP never set, not a guess", async () => {
    draw();

    await waitFor(() => expect(screen.getByText("QT FY27-013")).toBeTruthy());
    // `expires_on` and `opened_at` are both null on this quote.
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(2);
  });
});

describe("the lines", () => {
  it("are on the page — the reason somebody opens a quote", async () => {
    draw();

    await waitFor(() => expect(screen.getByText("CNMG120408")).toBeTruthy());
    expect(screen.getByText(/turning insert/)).toBeTruthy();
    expect(screen.getByText("Freight")).toBeTruthy();
  });

  it("stop showing the loading state once they arrive", async () => {
    draw();

    await waitFor(() => expect(screen.getByText("CNMG120408")).toBeTruthy());
    expect(document.querySelector(".MuiSkeleton-root")).toBeNull();
  });

  it("carry the server's reason when the breakdown has never been read", async () => {
    // The distinction an empty array cannot make: this quote's lines were not
    // pulled, which is not the same as this quote having none. An empty grid
    // would assert the second.
    erpQuoteLines.mockResolvedValue(lines({
      lines: [], lines_held: false,
      empty_reason: "The lines on this quote have not been read from your ERP yet.",
    }));
    draw();

    await waitFor(() =>
      expect(screen.getByText(/have not been read from your ERP yet/)).toBeTruthy());
    expect(document.querySelector(".MuiSkeleton-root")).toBeNull();
  });
});

describe("it always resolves", () => {
  it("says so when the read fails, rather than loading for ever", async () => {
    // The defect this file exists for. A skeleton that never resolves is
    // indistinguishable from a hang, and it is what the first version shipped.
    erpQuoteLines.mockRejectedValue(new Error("network down"));
    draw();

    await waitFor(() => expect(screen.getByText(/network down/)).toBeTruthy());
    expect(document.querySelector(".MuiSkeleton-root")).toBeNull();
  });

  it("says so when the reference names no quote this reader may see", async () => {
    listErpQuotes.mockResolvedValue({ quotes_listed: [] });
    draw();

    await waitFor(() => expect(screen.getByText("No such quote")).toBeTruthy());
    expect(document.querySelector(".MuiSkeleton-root")).toBeNull();
  });
});

describe("read-only, for anyone", () => {
  it("offers nothing to press but the way back", async () => {
    // Not "hides the edit button from a salesperson" — there is no edit button
    // for any role. A change typed here would be overwritten by the next sync,
    // so the page must not invite one.
    draw();

    await waitFor(() => expect(screen.getByText("CNMG120408")).toBeTruthy());
    const pressable = screen.getAllByRole("button")
      .map((b) => (b.textContent ?? "").trim())
      .filter((label) => label !== "");
    expect(pressable).toEqual(["All quotes"]);

    // Deliberately not "there are no textboxes": the line grid carries its own
    // per-column filter inputs, and a filter is a way of reading a long quote
    // rather than a way of changing one. What must not exist is a control that
    // writes, and the button list above is where one would show up.
    expect(screen.queryByRole("button", { name: /save|edit|send|delete/i }))
      .toBeNull();
  });

  it("says so in words as well", async () => {
    draw();

    await waitFor(() => expect(screen.getByText("CNMG120408")).toBeTruthy());
    expect(screen.getByText(/Nothing on this page can be edited/)).toBeTruthy();
  });
});

describe("the organization's own fields", () => {
  it("are labelled where this file knows the name", async () => {
    listErpQuotes.mockResolvedValue({
      quotes_listed: [quote({ attributes: { cf_quote_type: "Tender" } })] });
    draw();

    await waitFor(() => expect(screen.getByText("Quote type")).toBeTruthy());
    expect(screen.getByText("Tender")).toBeTruthy();
  });

  it("show a key this file has never heard of rather than dropping it", async () => {
    // It is a fact somebody typed into their ERP. Hiding it because this file
    // has no label for it would be this screen's own bug, repeated.
    listErpQuotes.mockResolvedValue({
      quotes_listed: [quote({ attributes: { cf_something_new: "Yes" } })] });
    draw();

    await waitFor(() => expect(screen.getByText("cf_something_new")).toBeTruthy());
    expect(screen.getByText("Yes")).toBeTruthy();
  });
});
