// The quote workspace: the list every desk shares, and what a row says.
//
// Two things are pinned here because they are the substance of the screen
// rather than its decoration: a draft with no customer is printed as a
// question still open ("No customer yet") rather than as a blank, and the
// send button appears on exactly the rows the server reported READY — the
// list must not offer to send what the gate would refuse.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { SnackbarProvider } from "notistack";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import QuoteWorkspace, { customerLabel } from "./QuoteWorkspace";
import type { PlatformSession, Role } from "./platform/types";
import type { QuoteDraftSummary } from "./types";

const listQuotes = vi.fn();
const listErpQuotes = vi.fn();
const createQuoteForm = vi.fn();
const createEstimate = vi.fn();
const navigate = vi.fn();

vi.mock("./api", async () => {
  const actual = await vi.importActual<typeof import("./api")>("./api");
  return {
    ...actual,
    forgetLegacyDraft: () => {},
    api: {
      listQuotes: (...a: unknown[]) => listQuotes(...a),
      // The screen fetches both lists on arrival: its own drafts, and what the
      // connected ERP raised. Stubbed empty here because these tests are about
      // the drafts tab — the ERP tab has its own file.
      listErpQuotes: (...a: unknown[]) => listErpQuotes(...a),
      createQuoteForm: (...a: unknown[]) => createQuoteForm(...a),
      createEstimate: (...a: unknown[]) => createEstimate(...a),
      deleteQuote: vi.fn(),
    },
  };
});

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

beforeEach(() => {
  listErpQuotes.mockResolvedValue({
    count: 0, by_outcome: { WON: 0, LOST: 0, UNRECORDED: 0 },
    value_total: null, quotes_without_a_value: 0,
    quotes_listed: [], listed: 0, currency: "INR", empty_reason: null,
  });
});

afterEach(() => {
  listQuotes.mockReset();
  listErpQuotes.mockReset();
  createQuoteForm.mockReset();
  createEstimate.mockReset();
  navigate.mockReset();
});

function session(role: Role = "OWNER"): PlatformSession {
  return {
    token: "tok", role, name: "Test", user_id: "u1", organization_id: "org1",
    currency: "INR", timezone: "Asia/Kolkata",
  };
}

function draft(over: Partial<QuoteDraftSummary>): QuoteDraftSummary {
  return {
    id: "q1", number: "QB-0001", customer: "Pitti Engineering", customerId: "c1",
    connectionId: null, company: "",
    lineCount: 3, unpriced: 0, total: 12000, readiness: "READY", sent: null,
    ownerId: "u1", owner: "R. Nair", canEdit: true,
    createdBy: "R. Nair", updatedBy: "R. Nair",
    createdAt: "2026-09-02T08:00:00Z", updatedAt: "2026-09-02T08:00:00Z",
    ...over,
  };
}

function mount() {
  return render(
    <MemoryRouter>
      <SnackbarProvider>
        <QuoteWorkspace session={session()} />
      </SnackbarProvider>
    </MemoryRouter>,
  );
}

describe("customerLabel", () => {
  it("prints a draft with no customer as a question still open, not a blank", () => {
    expect(customerLabel({ customer: "" })).toBe("No customer yet");
    expect(customerLabel({ customer: "   " })).toBe("No customer yet");
    expect(customerLabel({ customer: "Pitti" })).toBe("Pitti");
  });
});

describe("QuoteWorkspace", () => {
  // "New quote" opens a form and creates nothing. It used to write the row
  // and mint the number on the press, so opening the builder and changing your
  // mind left an empty quote on this shared list for good. What is pinned here
  // is which call it makes: `createQuote` still exists and still creates a
  // quote outright, and this button must not be the thing that reaches it.
  it("opens a blank form with no customer, creating no quote", async () => {
    listQuotes.mockResolvedValue([]);
    createQuoteForm.mockResolvedValue({ id: "f9", number: "", saved: false });
    mount();

    expect(await screen.findByText("No quotes yet")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Start the first quote/ }));

    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/quotes/f9"));
    // Opened empty: the customer is the builder's question, not this
    // screen's price of admission.
    expect(createQuoteForm).toHaveBeenCalledWith("tok", "", undefined, undefined);
  });

  it("offers to send exactly the rows the server reported ready", async () => {
    listQuotes.mockResolvedValue([
      draft({ id: "q1", number: "QB-0001", readiness: "READY" }),
      draft({ id: "q2", number: "QB-0002", readiness: "NEEDS_APPROVAL" }),
      draft({ id: "q3", number: "QB-0003", customer: "", customerId: null,
              readiness: "NO_CUSTOMER" }),
      // Ready, but somebody else's: no Send and no Remove for this reader.
      draft({ id: "q4", number: "QB-0004", readiness: "READY",
              ownerId: "u2", owner: "K. Iyer", canEdit: false }),
    ]);
    mount();

    // Cards, not the grid: the test viewport is narrower than the grid's
    // breakpoint, so the wrapper renders the narrow layout — which carries
    // the same controls, and is the half of the screen a phone gets.
    await screen.findByText("QB-0001");
    expect(screen.getAllByRole("button", { name: "Send" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Remove" })).toHaveLength(3);
    expect(screen.getByText("Needs approval")).toBeInTheDocument();
    // The status chip and the customer cell say different things about the
    // same fact, so neither is mistaken for the other.
    expect(screen.getByText("Needs a customer")).toBeInTheDocument();
    expect(screen.getByText("No customer yet")).toBeInTheDocument();
  });

  it("names the book on each draft only where the drafts span more than one company", async () => {
    const origin = (id: string, company: string) => ({
      connector: "zoho", connector_label: "Zoho Books", connector_short: "Zoho",
      icon: "◆", connection_id: id, company, external_id: "", unknown: false,
    });
    listQuotes.mockResolvedValue([
      draft({ id: "q1", number: "QB-0001", connectionId: "c-sls",
              company: "SLS Engineers", origin: origin("c-sls", "SLS Engineers") }),
      draft({ id: "q2", number: "QB-0002", connectionId: "c-4u",
              company: "4U Precision", origin: origin("c-4u", "4U Precision") }),
    ]);
    mount();
    await screen.findByText("QB-0001");
    expect(screen.getByText(/SLS Engineers/)).toBeInTheDocument();
    expect(screen.getByText(/4U Precision/)).toBeInTheDocument();
  });

  it("does not repeat one company's name down a single-company list", async () => {
    listQuotes.mockResolvedValue([
      draft({ id: "q1", number: "QB-0001", connectionId: "c-sls", company: "SLS Engineers" }),
      draft({ id: "q2", number: "QB-0002", connectionId: "c-sls", company: "SLS Engineers" }),
    ]);
    mount();
    await screen.findByText("QB-0001");
    expect(screen.queryByText(/SLS Engineers/)).not.toBeInTheDocument();
  });

  it("shows the document a sent draft became, and the ERP's own word once synced", async () => {
    listQuotes.mockResolvedValue([
      draft({ id: "q1", number: "QB-0001", readiness: "SENT",
              sent: { number: "EST-1001", systemLabel: "Zoho Books", current: true,
                      erp: { number: "EST-1001", sourceStatus: "sent", outcome: "UNRECORDED",
                             decidedOn: null, clientViewedAt: null } } }),
      draft({ id: "q2", number: "QB-0002", readiness: "SENT",
              sent: { number: "EST-1002", systemLabel: "Zoho Books", current: true, erp: null } }),
    ]);
    mount();
    await screen.findByText("QB-0001");
    expect(screen.getByText("EST-1001")).toBeInTheDocument();
    expect(screen.getByText("Zoho Books: sent")).toBeInTheDocument();
    // Unsynced: the number, and no word put in the ERP's mouth.
    expect(screen.getByText("EST-1002")).toBeInTheDocument();
    expect(screen.getAllByText(/Zoho Books:/)).toHaveLength(1);
  });

  it("sends from the list through the same endpoint the builder uses", async () => {
    listQuotes.mockResolvedValue([draft({ readiness: "READY" })]);
    createEstimate.mockResolvedValue({ ok: true, message: "Zoho Books estimate EST-7 created — 3 lines." });
    mount();

    fireEvent.click(await screen.findByRole("button", { name: "Send" }));
    await waitFor(() => expect(createEstimate).toHaveBeenCalledWith("tok", "q1"));
    expect(await screen.findByText(/EST-7 created/)).toBeInTheDocument();
    // The list is re-read so the row's status is the server's, not a guess.
    expect(listQuotes).toHaveBeenCalledTimes(2);
  });
});
