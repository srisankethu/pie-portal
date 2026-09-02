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
import { afterEach, describe, expect, it, vi } from "vitest";

import QuoteWorkspace, { customerLabel } from "./QuoteWorkspace";
import type { PlatformSession, Role } from "./platform/types";
import type { QuoteDraftSummary } from "./types";

const listQuotes = vi.fn();
const createQuote = vi.fn();
const createEstimate = vi.fn();
const navigate = vi.fn();

vi.mock("./api", async () => {
  const actual = await vi.importActual<typeof import("./api")>("./api");
  return {
    ...actual,
    forgetLegacyDraft: () => {},
    api: {
      listQuotes: (...a: unknown[]) => listQuotes(...a),
      createQuote: (...a: unknown[]) => createQuote(...a),
      createEstimate: (...a: unknown[]) => createEstimate(...a),
      deleteQuote: vi.fn(),
    },
  };
});

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

afterEach(() => {
  listQuotes.mockReset();
  createQuote.mockReset();
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
    lineCount: 3, unpriced: 0, total: 12000, readiness: "READY", sent: null,
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
  it("starts a draft with no customer and opens it", async () => {
    listQuotes.mockResolvedValue([]);
    createQuote.mockResolvedValue({ id: "q9", number: "QB-0009" });
    mount();

    expect(await screen.findByText("No quotes yet")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Start the first quote/ }));

    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/quotes/q9"));
    // Created empty: the customer is the builder's question, not this
    // screen's price of admission.
    expect(createQuote).toHaveBeenCalledWith("tok", "", undefined, undefined);
  });

  it("offers to send exactly the rows the server reported ready", async () => {
    listQuotes.mockResolvedValue([
      draft({ id: "q1", number: "QB-0001", readiness: "READY" }),
      draft({ id: "q2", number: "QB-0002", readiness: "NEEDS_APPROVAL" }),
      draft({ id: "q3", number: "QB-0003", customer: "", customerId: null,
              readiness: "NO_CUSTOMER" }),
    ]);
    mount();

    // Cards, not the grid: the test viewport is narrower than the grid's
    // breakpoint, so the wrapper renders the narrow layout — which carries
    // the same controls, and is the half of the screen a phone gets.
    await screen.findByText("QB-0001");
    expect(screen.getAllByRole("button", { name: "Send" })).toHaveLength(1);
    expect(screen.getByText("Needs approval")).toBeInTheDocument();
    // The status chip and the customer cell say different things about the
    // same fact, so neither is mistaken for the other.
    expect(screen.getByText("Needs a customer")).toBeInTheDocument();
    expect(screen.getByText("No customer yet")).toBeInTheDocument();
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
