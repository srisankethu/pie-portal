/** The foot of the quote: what it comes to, and whether it can go.
 *
 * The send used to look ready until it was pressed. Pressing it produced a
 * sentence about work done fourteen lines ago — "3 line(s) must be resolved" —
 * with nothing on screen saying which three, and a filter change that a reader
 * already on that filter could not even see happen.
 *
 * What is pinned here is the other half of the inline strips: the button counts
 * what is left *before* anybody presses it, says so in words, and refuses while
 * any of it stands. And the total says what it covers, because a subtotal over
 * thirteen of fourteen lines is not wrong and is not what somebody reading it
 * assumes.
 */
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SummaryBar } from "./SummaryBar";
import type { Blocker } from "./lineProblems";
import type { Quote } from "../types";

function quote(over: Partial<Quote> = {}): Quote {
  return {
    id: "q1", number: "QT-1", customer: "Bharat Forge", customerId: "c1",
    lines: [{ id: "l1" }],
    summary: {
      subtotal: 841600, tax: 151488, taxLabel: "GST", taxRate: 0.18,
      taxBasis: { known: 14, assumed: 0, defaultRate: 0.18 },
      grand: 993088, total: 14, unpriced: 0, atListPrice: 0,
    },
    filterCounts: {}, missingFields: [], canEdit: true,
    system: "zoho", systemLabel: "Zoho Books", systemShort: "Zoho",
    documentTerm: "estimate", booksLive: true, estimate: null,
    ...over,
  } as unknown as Quote;
}

function show(blockers: Blocker[], covers: string | null = null) {
  const onCreateEstimate = vi.fn();
  render(
    <ThemeProvider theme={createTheme()}>
      <SummaryBar
        quote={quote()}
        selectedCount={0}
        onDiscount={vi.fn()}
        onCreateEstimate={onCreateEstimate}
        busy={false}
        gateBlockedReason={null}
        blockers={blockers}
        covers={covers}
      />
    </ThemeProvider>,
  );
  return { onCreateEstimate };
}

describe("the send button", () => {
  it("says how many things remain, before anybody presses it", () => {
    show([
      { key: "unresolved", text: "2 lines have no item yet" },
      { key: "approval", text: "1 price needs signing off" },
    ]);
    const send = screen.getByRole("button", { name: /to settle first/ });
    expect(send).toHaveTextContent("2 to settle first");
    expect(send).toBeDisabled();
  });

  it("names them, so the button is a map rather than a count", () => {
    show([{ key: "unresolved", text: "2 lines have no item yet" }]);
    expect(screen.getByText(/2 lines have no item yet/)).toBeInTheDocument();
    expect(screen.getByText(/One thing to settle first/)).toBeInTheDocument();
  });

  it("sends when nothing is left", () => {
    show([]);
    const send = screen.getByRole("button", { name: /Send to Zoho Books/ });
    expect(send).toBeEnabled();
  });

  it("says what the total covers rather than letting the figure imply it", () => {
    show([], "Covers 13 of 14 lines. The other has no rate, so it is missing "
            + "from this total rather than counted as zero.");
    expect(screen.getByText(/Covers 13 of 14 lines/)).toBeInTheDocument();
  });
});
