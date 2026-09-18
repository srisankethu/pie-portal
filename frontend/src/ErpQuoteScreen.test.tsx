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
        item_name: "CNMG120408-MP KCP25",
        description: "CNMG 120408 MP KCP25 turning insert", product_id: "p1",
        qty: 10, unit: "pcs", rate: 450, amount: 4500 },
      // No catalogue item behind it, so no name — a real state, not a gap.
      { line_number: 1, item_code: "", item_name: "", description: "Freight",
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

/** Every diagnosis the stubbed engine will return, by line id. */
let diagnoses: Record<string, unknown> = {};
/** What the screen actually posted, and to where. */
let assessed: Record<string, unknown> | null = null;
let assessedPath: string | null = null;
/** Make `/assess` fail, with the server's own sentence. */
let assessFails: string | null = null;

function stubFetch() {
  return vi.fn(async (url: string, init?: RequestInit) => {
    if (String(url).includes("/quote-diagnosis/reasons")) {
      return { ok: true, json: async () => ({ reasons: [] }) } as Response;
    }
    if (String(url).includes("/quote-diagnosis/erp-quote/")) {
      assessedPath = String(url);
      assessed = JSON.parse(String(init?.body ?? "{}"));
      if (assessFails) {
        return { ok: false, statusText: "Unprocessable Content",
                 text: async () => JSON.stringify({ detail: assessFails }) } as Response;
      }
      return { ok: true, json: async () => ({
        quote_id: REF,
        lines: Object.values(diagnoses),
      }) } as Response;
    }
    throw new Error(`unexpected fetch: ${url}`);
  });
}

/** One diagnosis, in the shape the endpoint projects. */
/** The manager's projection — a different shape, not the same one filtered. */
function owner(over: Record<string, unknown> = {}) {
  return {
    view: "OWNER",
    quote_diagnosis_id: null, line_id: "0", renders: true, comparable: true,
    headline: "Above this customer's historical pricing",
    strength_word: "Strong",
    lines: ["Quoted ₹339 per unit against a supported range of ₹218."],
    opportunity: "No opportunity is asserted.",
    evidence: "11 usable, 0 excluded.",
    codes: ["ABOVE_HISTORICAL_RANGE"], context: [],
    qualification: "", actions: [],
    ...over,
  };
}

function diagnosis(over: Record<string, unknown> = {}) {
  return {
    view: "OPERATIONS",
    quote_diagnosis_id: null, line_id: "0", renders: true, comparable: true,
    headline: "Below this customer's historical pricing",
    quoted: "₹450", historical: "₹500 – ₹520",
    strength_word: "Strong",
    evidence: "Strong", evidence_detail: "14 comparable transactions",
    why: "This customer has purchased this item 14 times.",
    note: "", qualification: "", actions: [],
    ...over,
  };
}

beforeEach(() => {
  listErpQuotes.mockResolvedValue({ quotes_listed: [quote()] });
  erpQuoteLines.mockResolvedValue(lines());
  diagnoses = {};
  assessed = null;
  assessedPath = null;
  assessFails = null;
  vi.stubGlobal("fetch", stubFetch());
});
afterEach(() => {
  listErpQuotes.mockReset(); erpQuoteLines.mockReset(); vi.unstubAllGlobals();
});

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

  it("name the item before its code, which is what a reader recognises", async () => {
    // The screen showed the SKU alone. Nobody reads a quote by remembering
    // that 22000865 is a CNMG insert — the code is what gets checked against a
    // PO afterwards, which is a second act. Both are shown, name first.
    draw();

    await waitFor(() =>
      expect(screen.getByText("CNMG120408-MP KCP25")).toBeTruthy());
    expect(screen.getByText("CNMG120408")).toBeTruthy();
  });

  it("says so rather than going blank when a line is in no catalogue", async () => {
    // The freight line resolves to no product, so there is no master name for
    // it. Its code still names it; an empty name cell would read as a line
    // with nothing on it rather than a line the catalogue does not hold.
    draw();

    await waitFor(() => expect(screen.getByText("Freight")).toBeTruthy());
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
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

describe("the identity strip", () => {
  it("carries the quote, the customer and the book", () => {
    // The Quote Builder opens with the same three facts in the same place. A
    // reader should not have to re-learn where a quote's number lives because
    // this one came out of the ERP.
    draw();

    return waitFor(() => {
      expect(screen.getByText("Quote")).toBeTruthy();
      expect(screen.getByText("QT FY27-013")).toBeTruthy();
      expect(screen.getByText("Book")).toBeTruthy();
      expect(screen.getByText("SLS Engineers")).toBeTruthy();
    });
  });

  it("does not print the quote number twice", async () => {
    // It did: once as the page heading and once in the strip, ten millimetres
    // apart. The heading names the page now, as the Builder's does.
    draw();

    await waitFor(() => expect(screen.getByText("QT FY27-013")).toBeTruthy());
    expect(screen.getAllByText("QT FY27-013")).toHaveLength(1);
  });
});

describe("what the quote comes to", () => {
  it("adds the lines up, which this page never used to do at all", async () => {
    // Fourteen priced lines and nowhere on the screen saying what they came
    // to. 4500 + 500.
    draw();

    await waitFor(() => expect(screen.getByText("Lines total")).toBeTruthy());
    expect(screen.getByText("₹5,000")).toBeTruthy();
  });

  it("shows the ERP's own total for the document beside it", async () => {
    // Two different numbers on purpose: the lines are pre-tax and this is the
    // whole document. The page says so rather than computing the gap and
    // calling it tax, which is a thing this pull does not hold.
    draw();

    await waitFor(() => expect(screen.getByText("Quotation total")).toBeTruthy());
    expect(screen.getByText("₹1,01,139")).toBeTruthy();
  });

  it("leaves an unpriced line out of the total and says how many", async () => {
    // `sum(… or 0)` is the CLAUDE.md §1 tell. A line the ERP never priced is a
    // line nobody priced; adding it as zero puts a figure on screen that looks
    // complete and is not, with nothing saying so.
    //
    // The two priced amounts are chosen so their sum appears nowhere else on
    // the page — the grid renders every rate and every amount, so asserting a
    // total that equals one of them proves nothing about the total.
    erpQuoteLines.mockResolvedValue(lines({
      lines: [
        { line_number: 0, item_code: "A", item_name: "", description: "priced", product_id: null,
          qty: 1, unit: "pcs", rate: 6000, amount: 6000 },
        { line_number: 1, item_code: "B", item_name: "", description: "priced", product_id: null,
          qty: 1, unit: "pcs", rate: 1500, amount: 1500 },
        { line_number: 2, item_code: "C", item_name: "", description: "never priced",
          product_id: null, qty: 1, unit: "pcs", rate: null, amount: null },
      ],
    }));
    draw();

    await waitFor(() => expect(screen.getByText("₹7,500")).toBeTruthy());
    expect(screen.getByText(/1 line the ERP did not price/)).toBeTruthy();
  });

  it("invents no total when the breakdown was never read", async () => {
    // An absent breakdown is not a quote worth nothing, and the difference
    // between those two is the whole reason `lines_held` is on the wire.
    erpQuoteLines.mockResolvedValue(lines({
      lines: [], lines_held: false, empty_reason: "not read yet",
    }));
    draw();

    await waitFor(() =>
      expect(screen.getByText(/breakdown has not been read yet/)).toBeTruthy());
    expect(document.body.textContent).not.toMatch(/₹0(?!\d)/);
  });

  it("renders a total the ERP never gave as a dash, never as zero", async () => {
    listErpQuotes.mockResolvedValue({ quotes_listed: [quote({ value: null })] });
    erpQuoteLines.mockResolvedValue(lines({ lines: [], lines_held: false,
                                            empty_reason: "not read yet" }));
    draw();

    await waitFor(() => expect(screen.getByText("Quotation total")).toBeTruthy());
    expect(document.body.textContent).not.toMatch(/₹0(?!\d)/);
  });
});

describe("the diagnosis", () => {
  it("is on the page — the whole engine had no mount on this screen", async () => {
    diagnoses = { "0": diagnosis() };
    draw();

    await waitFor(() =>
      expect(screen.getByText(/Below this customer's historical pricing/))
        .toBeTruthy());
    expect(screen.getByText(/14 comparable transactions/)).toBeTruthy();
  });

  it("asks by quote reference, sending neither lines nor a date", async () => {
    // Both are read from the quote server-side, and that is the point rather
    // than tidiness: `as_of` on `/assess` is caller-supplied and therefore
    // bounded to a window, so every quote older than it came back refused. A
    // reference has nothing to walk.
    //
    // What the server then does with it — which lines are diagnosable, that
    // `as_of` is the quote's own raised date, and that nothing is recorded —
    // is pinned in `test_quote_diagnosis_erp.py`, where those decisions are
    // actually made. Asserting them here would be this screen claiming
    // something it no longer decides.
    draw();

    await waitFor(() => expect(assessedPath).not.toBeNull());
    expect(assessedPath).toContain(`/quote-diagnosis/erp-quote/${REF}`);
    expect(assessed).toEqual({});
  });

  it("says the check ran when it flagged nothing, rather than vanishing", async () => {
    // On the Builder an empty panel is right — a row reading "no findings" on
    // every ordinary quote is noise while somebody is pricing. On a finished
    // document a reader asking "was this checked?" cannot tell silence from a
    // panel that was never mounted, and that question is why this screen was
    // asked about twice.
    diagnoses = { "0": diagnosis({ renders: false }) };
    draw();

    await waitFor(() =>
      expect(screen.getByText(/nothing on those stood out/i)).toBeTruthy());
    expect(screen.getByText(/compared against what this customer had paid/))
      .toBeTruthy();
  });

  it("never reports a line it could not compare as one that looked fine", async () => {
    // The defect this replaced. A line renders no card when it sat inside the
    // supported range, when the deviation was too small to interrupt over, OR
    // when there was nothing to compare it against — and the screen reported
    // all three as "nothing stood out". Only the first two are good news.
    // `absence of evidence is not a pass`, over an engine that treats
    // INSUFFICIENT_EVIDENCE as a first-class answer.
    diagnoses = { "0": diagnosis({ renders: false, comparable: false }) };
    draw();

    await waitFor(() =>
      expect(screen.getByText(/No line on this quote could be compared/))
        .toBeTruthy());
    expect(screen.getByText(/absence of evidence, not a verdict/)).toBeTruthy();
    expect(screen.queryByText(/Nothing stood out/)).toBeNull();
  });

  it("counts the lines it could compare when only some had history", async () => {
    // The mixed case is the ordinary one on a real book, and the count is what
    // makes the sentence worth reading: "2 of 3" says how much of the quote the
    // clean verdict actually covers.
    erpQuoteLines.mockResolvedValue(lines({
      lines: [0, 1, 2].map((n) => ({
        line_number: n, item_code: `ITEM-${n}`, item_name: "",
        description: `line ${n}`,
        product_id: null, qty: 1, unit: "pcs", rate: 100 + n, amount: 100 + n,
      })),
    }));
    diagnoses = {
      "0": diagnosis({ line_id: "0", renders: false, comparable: true }),
      "1": diagnosis({ line_id: "1", renders: false, comparable: true }),
      "2": diagnosis({ line_id: "2", renders: false, comparable: false }),
    };
    draw();

    await waitFor(() => expect(screen.getByText(/2 of 3 lines were compared/))
      .toBeTruthy());
    expect(screen.getByText(/other 1 had no comparable history/)).toBeTruthy();
  });

  it("does not tell a manager a quote was clean when their own payload flagged it",
     async () => {
    // A manager is served the OWNER projection. It draws a card of its own now,
    // so the panel is not silent for them any more — but this sentence still
    // has to agree with the cards above it. It used to end "nothing stood out"
    // as a constant, which was true only for as long as no real quote surfaced
    // anything. QT-095 surfaced two.
    erpQuoteLines.mockResolvedValue(lines({
      lines: [0, 1].map((n) => ({
        line_number: n, item_code: `ITEM-${n}`, item_name: "",
        description: `line ${n}`,
        product_id: null, qty: 1, unit: "pcs", rate: 100 + n, amount: 100 + n,
      })),
    }));
    diagnoses = {
      "0": owner({ line_id: "0", renders: true, comparable: true }),
      "1": owner({ line_id: "1", renders: false, comparable: true }),
    };
    draw();

    await waitFor(() => expect(screen.getByText(/1 of them sits outside it/))
      .toBeTruthy());
    expect(screen.queryByText(/nothing on those stood out/i)).toBeNull();
  });

  it("still reports a genuinely clean quote as clean", async () => {
    // The other direction, so the fix above cannot be "never say clean".
    diagnoses = { "0": diagnosis({ renders: false, comparable: true }) };
    draw();

    await waitFor(() =>
      expect(screen.getByText(/nothing on those stood out/i)).toBeTruthy());
  });

  it("says so when the check could not run, and never reads as clean", async () => {
    // `absence of evidence is not a pass`, on a screen. A panel quiet because
    // it could not ask looks exactly like one quiet because there was nothing
    // to say, and the server's own sentence is the only thing that separates
    // them — here, the window it refuses to diagnose outside of.
    assessFails = "A diagnosis date must be within 90 days of today.";
    draw();

    await waitFor(() =>
      expect(screen.getByText(/within 90 days of today/)).toBeTruthy());
    expect(screen.queryByText(/Nothing on this quote stood out/)).toBeNull();
  });

  it("asks nothing at all when the breakdown was never read", async () => {
    erpQuoteLines.mockResolvedValue(lines({ lines: [], lines_held: false,
                                            empty_reason: "not read yet" }));
    draw();

    await waitFor(() => expect(screen.getByText("Quotation total")).toBeTruthy());
    expect(assessed).toBeNull();
    // And no claim that the check found nothing — it was never put the question.
    expect(screen.queryByText(/Nothing on this quote stood out/)).toBeNull();
  });
});
