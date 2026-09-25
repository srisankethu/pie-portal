/** The outcome bar under the Quote Builder's total.
 *
 * Two things earn the file. The ERP's own word now sits beside a person's
 * decision, with a button that records it — because where nobody here has
 * said, that word is already the outcome of record everywhere else, and the
 * bar was the one screen that could not see it. And the bar renders the
 * platform's one outcome form rather than a second of its own, which is what
 * lets it ask who won the business through the same fields the worklist uses.
 */
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { QuoteOutcomeBar } from "./QuoteOutcomeBar";
import type { ErpSide, QuoteOutcome } from "../types";

function outcome(over: Partial<QuoteOutcome> = {}): QuoteOutcome {
  return {
    quote_id: "q1", quote_document_ref: "est-1", status: "SENT", note: null,
    customer_ref: "Pitti Engineering", customer_id: "c1",
    loss_reason: null, lost_to: null, sent_at: "2026-09-10T08:00:00Z",
    decided_at: null, allowed_next: ["SENT", "WON", "LOST"],
    loss_reasons: ["PRICE", "DELIVERY", "COMPETITOR", "CUSTOMER_CANCELLED", "NO_DECISION"],
    ...over,
  };
}

function show(o: QuoteOutcome, erp: ErpSide | null = null) {
  const onRecord = vi.fn().mockResolvedValue(undefined);
  render(
    <ThemeProvider theme={createTheme()}>
      <QuoteOutcomeBar outcome={o} erp={erp} systemLabel="Zoho Books"
                       onRecord={onRecord} busy={false} />
    </ThemeProvider>,
  );
  return { onRecord };
}

async function choose(field: string, option: string) {
  fireEvent.mouseDown(screen.getByRole("combobox", { name: field }));
  fireEvent.click(await screen.findByRole("option", { name: option }));
}

describe("what the books say", () => {
  it("shows the ERP's decision beside an undecided quote and offers to record it", () => {
    show(outcome(), { number: "EST-1001", sourceStatus: "accepted", outcome: "WON",
                      decidedOn: "2026-09-14", clientViewedAt: null });
    expect(screen.getByText("Zoho Books say")).toBeInTheDocument();
    expect(screen.getByText(/accepted/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Record as won" })).toBeEnabled();
  });

  it("does not offer an undated ERP decision as one — the server's rule", () => {
    show(outcome(), { number: "EST-1001", sourceStatus: "accepted", outcome: "WON",
                      decidedOn: null, clientViewedAt: null });
    expect(screen.queryByText(/Zoho Books say/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Mark won" })).toBeEnabled();
  });

  it("says nothing about the books where nothing has been read back", () => {
    show(outcome());
    expect(screen.queryByText(/Zoho Books say/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Mark won" })).toBeEnabled();
  });

  it("does not argue with a decision a person already recorded", () => {
    show(outcome({ status: "LOST", loss_reason: "PRICE", lost_to: "Sandvik",
                   decided_at: "2026-09-15T08:00:00Z", allowed_next: [] }),
         { number: "EST-1001", sourceStatus: "accepted", outcome: "WON",
           decidedOn: "2026-09-14", clientViewedAt: null });
    expect(screen.queryByText(/Zoho Books say/)).not.toBeInTheDocument();
    expect(screen.getByText(/to Sandvik/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Record as won/ })).not.toBeInTheDocument();
  });
});

describe("recording a loss through the one outcome form", () => {
  it("asks the reason and who won it, and posts both", async () => {
    const { onRecord } = show(outcome());
    fireEvent.click(screen.getByRole("button", { name: "Mark lost…" }));
    const dialog = await screen.findByRole("dialog");
    await choose("Why we lost it", "Price — somebody quoted lower");
    fireEvent.change(within(dialog).getByLabelText(/Who won it/), {
      target: { value: "Sandvik" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Record" }));
    await waitFor(() => expect(onRecord).toHaveBeenCalledWith(
      "LOST", "PRICE", "Sandvik", undefined));
  });
});
