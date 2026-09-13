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

vi.mock("./api", async () => {
  const actual = await vi.importActual<typeof import("./api")>("./api");
  return {
    ...actual,
    api: {
      getQuote: (...a: unknown[]) => getQuote(...a),
      fieldDefinitions: (...a: unknown[]) => fieldDefinitions(...a),
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

afterEach(() => {
  getQuote.mockReset();
  fieldDefinitions.mockReset();
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
    id: "q1", customer: "", customerId: null, connectionId: null,
    number: "QB-0001", reference: "QB-0001", savedAt: "2026-09-02T08:00:00Z",
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

// The economics control, which is a toggle rather than an action: it turns the
// grid's cost and margin columns on and off, and it stays where it was left.
// It spent a while as a `Button` holding its pressed state in a hand-written
// `aria-pressed` next to a `variant` swapped by the same condition — two
// spellings of one fact, and the pair a screen reader reads is not the pair
// anybody looks at. So these assertions are about the announced state, not the
// paint: `ToggleButton` derives the attribute from `selected`, and the test
// fails if the control ever goes back to carrying its own.
describe("the economics toggle", () => {
  const economics = () => screen.getByRole("button", { name: "Economics" });

  async function openAsOwner() {
    getQuote.mockResolvedValue(quote());
    fieldDefinitions.mockResolvedValue([]);
    mount();
    await waitFor(() => expect(screen.getByText("QB-0001")).toBeInTheDocument());
  }

  // On by default for the role that has the numbers: the empty preference has
  // to mean "show me what I am pricing against", which is why the stored key is
  // the negative one.
  it("opens pressed, and says so", async () => {
    await openAsOwner();

    expect(economics()).toHaveAttribute("aria-pressed", "true");
  });

  it("releases on a press, and remembers it", async () => {
    await openAsOwner();

    fireEvent.click(economics());

    expect(economics()).toHaveAttribute("aria-pressed", "false");
    expect(window.localStorage.getItem("pie.quote.hide-economics")).toBe("1");
  });

  it("presses again on a second press, and forgets the preference", async () => {
    await openAsOwner();

    fireEvent.click(economics());
    fireEvent.click(economics());

    expect(economics()).toHaveAttribute("aria-pressed", "true");
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

    expect(screen.queryByRole("button", { name: "Economics" })).not.toBeInTheDocument();
  });
});
