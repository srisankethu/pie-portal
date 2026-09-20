// The Quote Builder: opening one.
//
// The screen has no test at all until here, and what it was missing is the
// cheapest one there is — that opening a draft renders the draft. The builder
// read its quote asynchronously and called a `useCallback` *below* the
// `if (!quote)` return, so the first render ran one hook fewer than the second
// and React threw "Rendered more hooks than during the previous render" the
// instant the quote landed. Nothing in the app catches that, so the whole tree
// unmounted and the tab went white.
//
// The assertions are deliberately about the screen being there rather than
// about its contents: a hook-order fault is invisible to every test that
// renders with the quote already in hand, which is why this one starts from a
// pending promise and waits.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { SnackbarProvider } from "notistack";
import { afterEach, describe, expect, it, vi } from "vitest";

import QuoteBuilder from "./QuoteBuilder";
import type { PlatformSession, Role } from "./platform/types";
import type { Line, Quote, QuoteFieldDefinition } from "./types";

const getQuote = vi.fn();
const fieldDefinitions = vi.fn();
const saveQuote = vi.fn();
const discardQuoteForm = vi.fn();
const createQuoteForm = vi.fn();
const navigate = vi.fn();

vi.mock("./api", async () => {
  const actual = await vi.importActual<typeof import("./api")>("./api");
  return {
    ...actual,
    api: {
      getQuote: (...a: unknown[]) => getQuote(...a),
      fieldDefinitions: (...a: unknown[]) => fieldDefinitions(...a),
      saveQuote: (...a: unknown[]) => saveQuote(...a),
      discardQuoteForm: (...a: unknown[]) => discardQuoteForm(...a),
      createQuoteForm: (...a: unknown[]) => createQuoteForm(...a),
    },
  };
});

// The assessment is a network call of its own and says nothing about whether
// the screen mounted. Held to an empty answer so the test is about the builder.
vi.mock("./intelligence", async () => {
  const actual = await vi.importActual<typeof import("./intelligence")>("./intelligence");
  return {
    ...actual,
    intelligence: {
      assess: vi.fn(() => new Promise(() => {})),
      gate: vi.fn(() => new Promise(() => {})),
      snapshot: vi.fn(),
      outcome: vi.fn(),
    },
  };
});

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

afterEach(() => {
  getQuote.mockReset();
  fieldDefinitions.mockReset();
  saveQuote.mockReset();
  discardQuoteForm.mockReset();
  createQuoteForm.mockReset();
  navigate.mockReset();
  vi.unstubAllGlobals();
  // The economics toggle is remembered in `localStorage`, which jsdom keeps for
  // the whole file. Left alone, the test that switches it off decides what the
  // next one opens on.
  window.localStorage.clear();
});

function session(role: Role = "OWNER"): PlatformSession {
  return {
    token: "tok", role, name: "Test", user_id: "u1", organization_id: "org1",
    currency: "INR", timezone: "Asia/Kolkata",
  };
}

/** An empty draft — the state a quote is in the moment it is started, which is
 *  exactly what "New quote" opens on. */
function quote(over: Partial<Quote> = {}): Quote {
  return {
    id: "q1", customer: "", customerId: null, connectionId: null, company: "",
    number: "QB-0001", saved: true, reference: "QB-0001",
    savedAt: "2026-09-02T08:00:00Z",
    system: "", systemLabel: "your books", systemShort: "books",
    documentTerm: "quote", booksLive: false,
    ownerId: "u1", owner: { id: "u1", name: "R. Nair" }, canEdit: true,
    fields: {}, missingFields: [], lines: [],
    summary: {
      subtotal: 0, tax: 0, taxLabel: "GST", taxRate: null,
      taxBasis: { known: 0, assumed: 0, defaultRate: 0.18 },
      grand: 0, total: 0, unpriced: 0, atListPrice: 0,
    },
    filterCounts: {}, estimate: null,
    ...over,
  };
}

function field(over: Partial<QuoteFieldDefinition> = {}): QuoteFieldDefinition {
  return {
    key: "po_ref", label: "Customer reference", kind: "TEXT",
    required: false, choices: [], ...over,
  } as QuoteFieldDefinition;
}

/** A settled line, so the phone case below renders cards rather than the empty
 *  state — the grid is where the narrow rendering lives. */
function line(): Line {
  return {
    id: "l1", raw: "CNMG 120408 KCP25 x 10", proposed: false, reading: "",
    reqCode: "CNMG120408", reqDesc: "Turning insert", reqQty: 10,
    rel: "EXACT", relLabel: "Identical",
    supplyCode: "2001174", supplyDesc: "CNMG 120408 MP KCP25",
    costBasis: null, customCostSet: false, sel: "AUTO",
    avail: 40, availUnknown: false, inBooks: true, shortage: null,
    quoted: 1000, priceSource: "USER", recommended: null, lineTotal: 10000,
    createPhase: null, service: null, incompatReason: null,
    status: { kind: "ready", label: "ready" },
    flags: {
      attention: false, procurement: false, missingBooks: false,
      manualReview: false, unresolved: false, substituted: false,
    },
    candidates: [], notes: [], substituted: false,
  };
}

/** A `matchMedia` that answers one question honestly: is the viewport narrower
 *  than the width the query names? jsdom has none at all, and MUI's
 *  `useMediaQuery` reads false without one — so every other test here takes the
 *  wide path, and installing this is the only way to reach the phone's. */
function pretendViewportIs(width: number) {
  vi.stubGlobal("matchMedia", (query: string) => {
    const max = /max-width:\s*([\d.]+)px/.exec(query);
    return {
      matches: max ? width <= Number.parseFloat(max[1]) : false,
      media: query, onchange: null,
      addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {},
      dispatchEvent: () => false,
    };
  });
}

function mount(role: Role = "OWNER") {
  return render(
    <MemoryRouter initialEntries={["/quotes/q1"]}>
      <SnackbarProvider>
        <Routes>
          <Route path="/quotes/:id" element={<QuoteBuilder session={session(role)} />} />
        </Routes>
      </SnackbarProvider>
    </MemoryRouter>,
  );
}

describe("opening a quote", () => {
  it("renders the draft once it arrives", async () => {
    getQuote.mockResolvedValue(quote());
    fieldDefinitions.mockResolvedValue([]);

    mount();

    // The skeleton first — this is the render where `quote` is still null.
    expect(screen.getByText("Opening the quote…")).toBeInTheDocument();
    // And then the quote itself, on the render where it is not.
    await waitFor(() => expect(screen.getByText("QB-0001")).toBeInTheDocument());
    expect(screen.getByText("Paste an RFQ to start building the quote")).toBeInTheDocument();
  });

  // The details panel returned `null` above its own `useState`, so the first
  // render with no definitions ran one hook fewer than the render after they
  // landed. Whichever of the two requests answers second decides whether the
  // screen survives, which is why this ordering is pinned rather than left to
  // the network.
  it("survives the field definitions arriving after the quote", async () => {
    getQuote.mockResolvedValue(quote());
    let land: (d: QuoteFieldDefinition[]) => void = () => {};
    fieldDefinitions.mockReturnValue(
      new Promise<QuoteFieldDefinition[]>((r) => { land = r; }));

    mount();
    await waitFor(() => expect(screen.getByText("QB-0001")).toBeInTheDocument());

    land([field()]);
    await waitFor(() => expect(screen.getByText("Quote details")).toBeInTheDocument());
    expect(screen.getByText("QB-0001")).toBeInTheDocument();
  });

  // The report was "opening a quote on mobile is a blank screen", so the phone
  // is the viewport worth pinning: 412px is the Pixel 7 the grid's own narrow
  // rendering was measured on. The fault was never mobile-only — it was the
  // hook order, and it fired on every device — but this is the path somebody
  // actually walked, and it reaches the cards rather than the grid.
  it("renders a quote with lines on a phone", async () => {
    pretendViewportIs(412);
    getQuote.mockResolvedValue(quote({ lines: [line()], filterCounts: { ALL: 1 } }));
    fieldDefinitions.mockResolvedValue([]);

    mount();

    await waitFor(() => expect(screen.getByText("QB-0001")).toBeInTheDocument());
    // The cards, not the grid: `DataGrid` gives the narrow rendering its own
    // `role="list"`, so this asserts the branch as well as the content.
    expect(screen.getByRole("list", { name: "Quote lines" })).toBeInTheDocument();
    expect(screen.getByText("CNMG 120408 MP KCP25")).toBeInTheDocument();
    expect(screen.getByText("Quotation total")).toBeInTheDocument();
  });

  it("says why a draft could not be opened", async () => {
    getQuote.mockRejectedValue(new Error("That quote is not in this workspace"));
    fieldDefinitions.mockResolvedValue([]);

    mount();

    await waitFor(() =>
      expect(screen.getByText("This quote could not be opened")).toBeInTheDocument());
    expect(screen.getByText("That quote is not in this workspace")).toBeInTheDocument();
  });
});

// Pressing "New quote" creates nothing now: the builder opens on a form, and
// Save is the only thing on the screen that makes a quote. What is pinned here
// is the screen's half of that — the server's half is
// `backend/tests/test_quote_form.py`, which counts rows rather than pixels.
describe("a quote that has not been saved", () => {
  const form = (over: Partial<Quote> = {}) =>
    quote({ saved: false, number: "", savedAt: null, ...over });

  it("says so, and offers Save rather than a number", async () => {
    getQuote.mockResolvedValue(form());
    fieldDefinitions.mockResolvedValue([]);

    mount();

    await waitFor(() =>
      expect(screen.getByText("This quote has not been saved yet")).toBeInTheDocument());
    // No number is printed, because none has been minted. One somebody could
    // write down and then fail to find is worse than none.
    expect(screen.getByText("Not saved yet")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /Save quote/ }).length)
      .toBeGreaterThan(0);
  });

  it("creates the quote on Save and moves to it", async () => {
    getQuote.mockResolvedValue(form({ id: "f1" }));
    fieldDefinitions.mockResolvedValue([]);
    saveQuote.mockResolvedValue(quote({ id: "q1", number: "QB-0007" }));

    mount();
    await waitFor(() => expect(screen.getByText("Not saved yet")).toBeInTheDocument());

    fireEvent.click(screen.getAllByRole("button", { name: /Save quote/ })[0]);

    await waitFor(() => expect(saveQuote).toHaveBeenCalledWith("tok", "f1"));
    // The saved quote has its own id, so the URL is replaced rather than
    // pushed: Back must not return to a form id that no longer resolves.
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith("/quotes/q1", { replace: true }));
    expect(await screen.findByText("QB-0007")).toBeInTheDocument();
  });

  it("will not send until it has been saved", async () => {
    // Everything else about this quote is ready — a priced line and a customer
    // — so being unsaved is the only thing left, and the bar has to say it.
    getQuote.mockResolvedValue(form({
      customer: "Bharat Forge", customerId: "c1",
      lines: [line()], filterCounts: { ALL: 1 },
    }));
    fieldDefinitions.mockResolvedValue([]);

    const { container } = mount();
    await waitFor(() => expect(screen.getByText("Not saved yet")).toBeInTheDocument());

    // The send counts it as a blocker like any other, so the button says how
    // many things remain rather than offering to send and then refusing.
    expect(container.textContent).toContain("the quote has not been saved yet");
    expect(screen.getByRole("button", { name: "1 to settle first" })).toBeDisabled();
  });

  it("discards a form with work in it only after asking", async () => {
    getQuote.mockResolvedValue(form({ id: "f1", lines: [line()],
                                      filterCounts: { ALL: 1 } }));
    fieldDefinitions.mockResolvedValue([]);
    discardQuoteForm.mockResolvedValue({ ok: true });

    mount();
    await waitFor(() => expect(screen.getByText("Not saved yet")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(await screen.findByText("Discard this quote?")).toBeInTheDocument();
    // Still nothing discarded — the question is a question.
    expect(discardQuoteForm).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    await waitFor(() => expect(discardQuoteForm).toHaveBeenCalledWith("tok", "f1"));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/quotes"));
  });

  it("closes an untouched form without a question", async () => {
    // A confirmation people always dismiss is one they stop reading, so a form
    // with nothing in it goes straight out.
    getQuote.mockResolvedValue(form({ id: "f1" }));
    fieldDefinitions.mockResolvedValue([]);
    discardQuoteForm.mockResolvedValue({ ok: true });

    mount();
    await waitFor(() => expect(screen.getByText("Not saved yet")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(discardQuoteForm).toHaveBeenCalledWith("tok", "f1"));
    expect(screen.queryByText("Discard this quote?")).not.toBeInTheDocument();
  });
});

// The builder's own "New quote" button. A saved quote is left where it is; an
// unsaved form is thrown away first, because leaving it behind is how the
// orphan rows this whole change removes would come back.
describe("starting another quote from inside the builder", () => {
  it("discards the unsaved form it was on, after asking", async () => {
    getQuote.mockResolvedValue(
      quote({ id: "f1", saved: false, number: "", savedAt: null,
              lines: [line()], filterCounts: { ALL: 1 } }));
    fieldDefinitions.mockResolvedValue([]);
    discardQuoteForm.mockResolvedValue({ ok: true });
    createQuoteForm.mockResolvedValue(
      quote({ id: "f2", saved: false, number: "", savedAt: null }));

    mount();
    await waitFor(() => expect(screen.getByText("Not saved yet")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "New quote" }));
    fireEvent.click(await screen.findByRole(
      "button", { name: "Discard and start a new one" }));

    await waitFor(() => expect(discardQuoteForm).toHaveBeenCalledWith("tok", "f1"));
    await waitFor(() => expect(createQuoteForm).toHaveBeenCalled());
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/quotes/f2"));
  });

  it("leaves a saved quote alone", async () => {
    getQuote.mockResolvedValue(quote({ id: "q1" }));
    fieldDefinitions.mockResolvedValue([]);
    createQuoteForm.mockResolvedValue(
      quote({ id: "f2", saved: false, number: "", savedAt: null }));

    mount();
    await waitFor(() => expect(screen.getByText("QB-0001")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "New quote" }));

    await waitFor(() => expect(createQuoteForm).toHaveBeenCalled());
    // Nothing was discarded, and no question was asked: a saved quote stays in
    // the workspace whatever else the desk does next.
    expect(discardQuoteForm).not.toHaveBeenCalled();
    expect(screen.queryByText("Discard this quote?")).not.toBeInTheDocument();
  });
});

// The economics control, which is a toggle rather than an action: it turns the
// grid's cost and margin columns on and off, and it stays where it was left.
//
// It was a `Button` swapping `variant` with a hand-written `aria-pressed`
// beside it, then a `ToggleButton`, and both were read off the screen as
// buttons — correctly, because both draw a rectangular bordered control whose
// only state cue is its fill. It is a `Switch` now.
//
// `getByRole("switch")` is the assertion that holds that: it passes only for a
// control that reports itself as one, so a change back to anything
// button-shaped fails here rather than in somebody's report. The state
// assertions read `aria-checked` for the same reason as before — MUI derives
// it from `checked`, so what a screen reader announces cannot drift from what
// is drawn.
describe("the economics toggle", () => {
  const economics = () => screen.getByRole("switch", { name: "Economics" });

  async function openAsOwner() {
    getQuote.mockResolvedValue(quote());
    fieldDefinitions.mockResolvedValue([]);
    mount();
    await waitFor(() => expect(screen.getByText("QB-0001")).toBeInTheDocument());
  }

  // On by default for the role that has the numbers: the empty preference has
  // to mean "show me what I am pricing against", which is why the stored key is
  // the negative one.
  it("opens on, and says so", async () => {
    await openAsOwner();

    expect(economics()).toBeChecked();
  });

  it("switches off on a click, and remembers it", async () => {
    await openAsOwner();

    fireEvent.click(economics());

    expect(economics()).not.toBeChecked();
    expect(window.localStorage.getItem("pie.quote.hide-economics")).toBe("1");
  });

  it("switches back on, and forgets the preference", async () => {
    await openAsOwner();

    fireEvent.click(economics());
    fireEvent.click(economics());

    expect(economics()).toBeChecked();
    expect(window.localStorage.getItem("pie.quote.hide-economics")).toBeNull();
  });

  // Not a control this role is offered, because it is not a control over
  // anything: the server sends a salesperson no cost and no margin, so both
  // columns are absent whichever way the toggle sits. The authority is
  // `require_manager_or_owner` on the endpoint; this only mirrors it.
  it("is not offered to a salesperson", async () => {
    getQuote.mockResolvedValue(quote());
    fieldDefinitions.mockResolvedValue([]);

    mount("SALESPERSON");
    await waitFor(() => expect(screen.getByText("QB-0001")).toBeInTheDocument());

    expect(screen.queryByRole("switch", { name: "Economics" })).not.toBeInTheDocument();
  });
});
