// Three properties, and all three are about refusing to invent something.
//
// This screen exists to grow a sample of six recorded losses, and the two ways
// it could quietly corrupt what it grows are the two `CLAUDE.md` §1 names: an
// absence rendered as a zero, and a loss filed without the reason that makes it
// countable. The third test is about the ranking — the server publishes
// `group_order` so the list can be recomputed rather than inferred, and a
// hardcoded copy in the client is a copy that disagrees the first time the
// ordering argument changes.
//
// **Rendered narrow on purpose.** `DataGrid` draws one card per row below
// `NARROW_BREAKPOINT` and the ag-grid chunk is never fetched, which is what
// makes these assertions about *this* component rather than about ag-grid's
// behaviour inside jsdom. The grid's own cells read `ageLabel` and `valueLabel`
// — the same two functions the cards read — so the first test also covers the
// wide rendering, and it asserts against them directly to say so.
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { SnackbarProvider } from "notistack";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { intelligence } from "../intelligence";
import { papi } from "./api";
import { UnrecordedQuotesScreen, ageLabel } from "./UnrecordedQuotes";
import type {
  PlatformSession, UnrecordedQuote, UnrecordedQuoteGroup, UnrecordedQuotes,
} from "./types";

const SESSION: PlatformSession = {
  token: "t", role: "SALESPERSON", name: "R. Iyer", user_id: "u1",
  organization_id: "org_pie", currency: "INR", timezone: "Asia/Kolkata",
};

function quote(over: Partial<UnrecordedQuote>): UnrecordedQuote {
  return {
    quote_document_ref: "zoho-1",
    number: "EST-0001",
    customer_id: "cst_1",
    customer_label: "Pitti Engineering",
    source_status: "sent",
    raised_on: "2026-01-05",
    expires_on: "2026-02-05",
    group: "PAST_EXPIRY",
    days_past_expiry: 40,
    value: 120000,
    opened_at: null,
    ...over,
  };
}

/** The quote this whole screen is careful about: the ERP recorded no expiry
 *  date, so how long it has been sitting is unanswerable — not zero — and it
 *  gave no total either, so it is real quoting activity worth nothing known
 *  rather than worth nothing. */
const NO_EXPIRY = quote({
  quote_document_ref: "zoho-2",
  number: "EST-0002",
  customer_label: "Rane Brake Lining",
  expires_on: null,
  group: "EXPIRY_NOT_RECORDED",
  days_past_expiry: null,
  value: null,
});

function payload(over: Partial<UnrecordedQuotes> = {}): UnrecordedQuotes {
  const quotes = over.quotes ?? [quote({}), NO_EXPIRY];
  return {
    count: 215,
    by_group: { PAST_EXPIRY: 180, EXPIRY_NOT_RECORDED: 20, STILL_OPEN: 15 },
    value_at_stake: 4200000,
    quotes_without_a_value: 7,
    longest_lapse_days: 310,
    opened: 40,
    opening_not_recorded: 175,
    listed: quotes.length,
    as_of: "2026-03-17",
    group_order: ["PAST_EXPIRY", "EXPIRY_NOT_RECORDED", "STILL_OPEN"],
    empty_reason: null,
    currency: "INR",
    thresholds_version: "ci_abc",
    ...over,
    quotes,
  };
}

/** Report a narrow viewport, so `DataGrid` draws its cards rather than lazily
 *  fetching ag-grid. jsdom has no `matchMedia` at all, and MUI's fallback
 *  answers `false` to everything — which would put every test on the wide path. */
function narrowViewport() {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query.includes("max-width"), media: query, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {},
    addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  }));
}

function show(view: UnrecordedQuotes) {
  narrowViewport();
  vi.spyOn(papi, "unrecordedQuotes").mockResolvedValue(view);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <SnackbarProvider>
        <ThemeProvider theme={createTheme()}>
          <UnrecordedQuotesScreen session={SESSION} />
        </ThemeProvider>
      </SnackbarProvider>
    </QueryClientProvider>);
}

describe("an age nobody can compute", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("says so, and never says zero", async () => {
    show(payload());
    expect(await screen.findByText("Rane Brake Lining")).toBeInTheDocument();

    // The row with no expiry date says the date is missing. The failure this
    // guards is the one `unrecorded.py` refuses one layer down: a `?? 0` here
    // would file a quote nobody has an age for with the freshest in the book.
    expect(screen.getByText(/no expiry date on record/)).toBeInTheDocument();
    // An exact match, not a substring: "310 days" is the longest lapse on the
    // book and a loose regex would find a zero that is not there.
    expect(screen.queryByText("0 days")).not.toBeInTheDocument();
    expect(screen.queryByText(/lapsed 0 day/)).not.toBeInTheDocument();

    // A missing total is the same refusal about money, and it is not ₹0.
    expect(screen.getByText(/No total on the quote/)).toBeInTheDocument();
    expect(screen.queryByText(/₹0/)).not.toBeInTheDocument();

    // The row that does have both still reads as a lapse of the server's own
    // number, so the test above is about the absence rather than about the
    // component rendering nothing at all.
    expect(screen.getByText(/lapsed 40 days ago/)).toBeInTheDocument();
  });

  it("gives the grid's cells the same three answers as the cards", () => {
    // The wide rendering reads `ageLabel` in its cell renderer and the narrow
    // card reads it too, which is the only thing that keeps the two from
    // drifting. Asserted directly because ag-grid does not render in jsdom, and
    // a property that only holds on the path the tests happen to take is not a
    // property.
    expect(ageLabel(quote({ days_past_expiry: 40 }))).toBe("40 days");
    expect(ageLabel(quote({ days_past_expiry: 1 }))).toBe("1 day");
    // No expiry on record: unanswerable.
    expect(ageLabel(NO_EXPIRY)).toBe("No expiry set");
    // An expiry still ahead: not a lapse of zero days, and not a lapse at all.
    expect(ageLabel(quote({
      group: "STILL_OPEN", expires_on: "2026-09-01", days_past_expiry: null,
    }))).toBe("Not lapsed");
  });
});

describe("recording a loss", () => {
  beforeEach(() => vi.restoreAllMocks());

  async function openTheDialog() {
    show(payload({ quotes: [quote({})] }));
    fireEvent.click(await screen.findByRole("button",
                                            { name: "Record what happened" }));
    return screen.getByRole("dialog");
  }

  async function choose(field: string, option: string) {
    fireEvent.mouseDown(screen.getByRole("combobox", { name: field }));
    fireEvent.click(await screen.findByRole("option", { name: option }));
  }

  it("will not let a loss be filed without a reason", async () => {
    const dialog = await openTheDialog();
    await choose("Outcome", "Lost");

    // The five, and only the five. `NOT_RECORDED` is a reading state for losses
    // decided before the vocabulary existed and must never be offered.
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Why we lost it" }));
    expect(await screen.findAllByRole("option")).toHaveLength(5);
    expect(screen.queryByRole("option", { name: /Not recorded/ }))
      .not.toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "Escape" });

    // Unmissable rather than merely required: the button that files it does not
    // work until the question is answered.
    const record = within(dialog).getByRole("button", { name: "Record" });
    await waitFor(() => expect(record).toBeDisabled());

    await choose("Why we lost it", "Price — somebody quoted lower");
    await waitFor(() => expect(record).toBeEnabled());
  });

  it("shows the server's own refusal, word for word", async () => {
    // The client guard above is a convenience; the server owns the rule. When
    // it refuses, the sentence is the whole value of the answer — it names
    // every reason a person may choose — and a paraphrase would leave somebody
    // with a form that says no and will not say what makes it say yes.
    const refusal = "A LOST outcome needs loss_reason, one of: PRICE, DELIVERY, "
      + "COMPETITOR, CUSTOMER_CANCELLED, NO_DECISION.";
    vi.spyOn(intelligence, "documentOutcome").mockRejectedValue(new Error(refusal));

    const dialog = await openTheDialog();
    await choose("Outcome", "Lost");
    await choose("Why we lost it", "Price — somebody quoted lower");
    fireEvent.click(within(dialog).getByRole("button", { name: "Record" }));

    expect(await screen.findByText(refusal)).toBeInTheDocument();
    // Still open, still holding what was typed: a refusal that closed the form
    // would make the reader start again to read it.
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("names the ERP's own reference, not a platform quote id", async () => {
    const write = vi.spyOn(intelligence, "documentOutcome").mockResolvedValue(
      { quote_id: null, quote_document_ref: "zoho-1", status: "LOST",
        note: null, loss_reason: "PRICE", lost_to: null, sent_at: null,
        decided_at: "2026-03-17T00:00:00Z", allowed_next: [],
        loss_reasons: [] });

    const dialog = await openTheDialog();
    await choose("Outcome", "Lost");
    await choose("Why we lost it", "Price — somebody quoted lower");
    fireEvent.click(within(dialog).getByRole("button", { name: "Record" }));

    // `quote_document_ref`, never `quote_documents.quote_document_id`: the
    // surrogate is re-minted by a full re-sync and an outcome written to it
    // would lose its quote on the next rebuild.
    await waitFor(() => expect(write).toHaveBeenCalledWith(
      "t", "zoho-1", "LOST", "Pitti Engineering", undefined, "PRICE"));
  });
});

describe("the order of the piles", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("is the server's, in the order the server sent it", async () => {
    // Deliberately not the order `unrecorded.GROUP_ORDER` ships with. A screen
    // that hardcoded the three names would pass with the default and fail here,
    // which is the only way to tell the two apart.
    const shuffled: UnrecordedQuoteGroup[] =
      ["STILL_OPEN", "PAST_EXPIRY", "EXPIRY_NOT_RECORDED"];
    show(payload({ group_order: shuffled }));

    const chips = within(await screen.findByRole("group", { name: "Which pile" }))
      .getAllByRole("button");
    // "All" leads, then the server's order. `FilterChip` puts the count in the
    // avatar slot, which precedes the label in the DOM.
    expect(chips.map((c) => c.textContent)).toEqual([
      "215All", "15Still open", "180Lapsed", "20No expiry recorded",
    ]);
  });

  it("says how big the pile is and how much of it is on the page", async () => {
    // `build()` returns the whole list and the router slices it precisely so
    // this sentence can be true. A headline that agreed with the visible rows
    // would make 215 unanswered quotes look like two.
    show(payload());
    expect(await screen.findByText(/Showing the top 2\./)).toBeInTheDocument();
    expect(screen.getByText(/The other 213 are real and not on this page\./))
      .toBeInTheDocument();
  });

  it("explains an empty screen instead of showing nothing", async () => {
    const why = "Every quote this book holds has an outcome the ERP recorded, "
      + "or no quotes have been synced yet.";
    show(payload({
      quotes: [], count: 0, listed: 0, empty_reason: why,
      by_group: { PAST_EXPIRY: 0, EXPIRY_NOT_RECORDED: 0, STILL_OPEN: 0 },
    }));
    // The server's sentence, because only the server can tell "nothing synced"
    // from "everything already answered".
    expect(await screen.findByText(why)).toBeInTheDocument();
  });
});
