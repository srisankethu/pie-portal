// The builder survives the quote arriving.
//
// One assertion, and the reason it is worth a file. `QuoteBuilder` renders
// twice on every visit: once with `quote` null, while `api.getQuote` is out,
// and again when it answers. The screen's first version of `approvalPendingFor`
// was a `useCallback` written down beside the other line actions — which is
// *below* `if (!quote) return …`, so it ran on the second render and not the
// first. React counts hooks per render and threw "Rendered more hooks than
// during the previous render" on the spot, with no error boundary above it, so
// the whole application unmounted: a white page from the press of "New quote"
// onwards, at every viewport, and every later screen in the same session blank
// because the root had gone.
//
// Nothing else in the suite renders this component, which is how a crash that
// obvious reached main. Types cannot see it, the build cannot see it, and it
// reads as ordinary code — the hook is simply in the wrong half of the file.
// So this mounts the real component through the real state change and asserts
// it arrives: if a hook is ever added below that return again, this goes red
// here rather than on the first phone that opens a quote.
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { SnackbarProvider } from "notistack";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { afterEach, describe, expect, it, vi } from "vitest";

import QuoteBuilder from "./QuoteBuilder";
import type { PlatformSession } from "./platform/types";
import type { Quote } from "./types";

const getQuote = vi.fn();

vi.mock("./api", async () => {
  const actual = await vi.importActual<typeof import("./api")>("./api");
  return {
    ...actual,
    api: {
      getQuote: (...a: unknown[]) => getQuote(...a),
      fieldDefinitions: async () => [],
    },
  };
});

// The assessment is the platform's answer about a quote, fetched over HTTP the
// moment one is on screen. Its own tests cover what it asks and when; here it
// would only be a second network mock, so it is stubbed at the seam.
vi.mock("./useQuoteIntelligence", () => ({
  useQuoteIntelligence: () => ({
    data: null, byLineId: {}, loading: false, error: null,
    recordOverride: async () => {}, requestApproval: async () => {},
    recordOutcome: async () => {}, gate: null, refresh: () => {},
  }),
}));

// One `navigate`, not a new one per render: the load effect lists it in its
// dependencies, so a mock that returns a fresh function every time re-runs the
// effect on every render and the screen never leaves its loading state.
const navigate = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useParams: () => ({ id: "q1" }), useNavigate: () => navigate };
});

afterEach(() => getQuote.mockReset());

const SESSION: PlatformSession = {
  token: "tok", role: "SALES_MANAGER", name: "M. Rao", user_id: "u1",
  organization_id: "org1", currency: "INR", timezone: "Asia/Kolkata",
};

/** A draft as the server answers it: one line, nothing priced yet — the state
 *  a quote is in for the whole of the work this screen exists for. */
function quote(): Quote {
  return {
    id: "q1", number: "QB-0005", customer: "Pitti Engineering Ltd", customerId: "c1",
    connectionId: null, reference: "QB-0005", savedAt: "2026-09-12T16:19:00Z",
    lines: [{
      id: "l1", raw: "2001174, 20", proposed: false, reading: "",
      reqCode: "2001174", reqDesc: "", reqQty: 20,
      rel: null, relLabel: "", supplyCode: null, supplyDesc: "",
      costBasis: null, customCostSet: false, sel: "AUTO",
      avail: null, availUnknown: true, inBooks: false, shortage: null,
      quoted: null, priceSource: null, recommended: null, lineTotal: null,
      createPhase: null, service: null, incompatReason: null,
      status: { kind: "unresolved", label: "unresolved" },
      flags: {
        attention: true, procurement: false, missingBooks: false,
        manualReview: false, unresolved: true, substituted: false,
      },
      candidates: [], notes: [], substituted: false, economics: null,
    }],
    summary: {
      subtotal: 0, tax: 0, taxLabel: "GST", taxRate: null,
      taxBasis: { known: 0, assumed: 0, defaultRate: 0.18 },
      grand: 0, total: 0, unpriced: 1, atListPrice: 0,
    },
    filterCounts: {}, missingFields: [], fields: {}, canEdit: true,
    system: "zoho", systemLabel: "Zoho Books", systemShort: "Zoho",
    documentTerm: "estimate", booksLive: false, estimate: null,
    ownerId: "u1", owner: { id: "u1", name: "M. Rao" },
  } as unknown as Quote;
}

function mount() {
  return render(
    <ThemeProvider theme={createTheme()}>
      <MemoryRouter>
        <SnackbarProvider>
          <QuoteBuilder session={SESSION} />
        </SnackbarProvider>
      </MemoryRouter>
    </ThemeProvider>,
  );
}

describe("the Quote Builder", () => {
  it("renders the draft that arrives after the first paint", async () => {
    getQuote.mockResolvedValue(quote());
    mount();
    // Before: the loading state, with no quote to draw.
    expect(screen.getByText("Opening the quote…")).toBeInTheDocument();
    // After: the draft itself. Reaching this at all is the assertion — a hook
    // below the early return throws during this render.
    await waitFor(() => expect(screen.getByText("QB-0005")).toBeInTheDocument());
    expect(screen.getByText("Pitti Engineering Ltd")).toBeInTheDocument();
  });

  it("says so, and offers the way back, when the quote cannot be opened", async () => {
    getQuote.mockRejectedValue(new Error("No quote q1"));
    mount();
    await waitFor(() =>
      expect(screen.getByText("This quote could not be opened")).toBeInTheDocument());
  });
});
