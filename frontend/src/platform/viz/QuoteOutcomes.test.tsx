// One property: what the dialog collects, this screen forwards — all of it.
//
// `RecordOutcomeDialog` asks "Who won it, if you know" on a loss and hands four
// answers to `onRecord`. The callback here was written when there were three,
// and when the fourth arrived every other caller was re-read and this one was
// not: the name was collected, the writer was called without it, and
// `quote_outcomes.lost_to` was written NULL. WON and LOST are terminal, so the
// person could not go back and say it again. A competitor named on this screen
// never reached the competitor mix.
//
// `DataGrid` is replaced by a plain renderer that calls each column's
// `cellRenderer`, because ag-grid does not render under jsdom and the button
// that opens the dialog lives in a cell. Nothing else is faked: the real
// dialog, the real screen, the real client up to `fetch`.
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { SnackbarProvider } from "notistack";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { intelligence } from "../../intelligence";
import { defineAbilityFor } from "../ability";
import { papi } from "../api";
import { GroupScopeProvider } from "../groupScope";
import { QuoteOutcomesScreen } from "./QuoteOutcomes";
import type { PlatformSession } from "../types";

vi.mock("../DataGrid", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../DataGrid")>();
  function FakeGrid<T>({ rows, columns, ariaLabel, getRowId }: {
    rows: T[] | null; columns: Array<Record<string, unknown>>; ariaLabel: string;
    getRowId?: (r: T) => string;
  }) {
    if (!rows) return null;
    return (
      <div role="table" aria-label={ariaLabel}>
        {rows.map((row, i) => (
          <div role="row" key={getRowId ? getRowId(row) : i}>
            {columns.map((col, j) => {
              const render = col.cellRenderer as
                ((p: { data?: T }) => React.ReactNode) | undefined;
              const field = col.field as string | undefined;
              return (
                <span role="cell" key={j}>
                  {render ? render({ data: row })
                    : field ? String((row as Record<string, unknown>)[field] ?? "")
                    : null}
                </span>
              );
            })}
          </div>
        ))}
      </div>
    );
  }
  return { ...mod, DataGrid: FakeGrid };
});

const SESSION: PlatformSession = {
  token: "t", role: "SALESPERSON", name: "R. Iyer", user_id: "u1",
  organization_id: "org_pie", currency: "INR", timezone: "Asia/Kolkata",
};

/** The server's own vocabulary, in the shape `reason_catalogue` carries it. */
const CATALOGUE = {
  PRICE: { label: "Price — somebody quoted lower",
           meaning: "Counts as spend that went to a competitor." },
  DELIVERY: { label: "Delivery — somebody could supply and we could not",
              meaning: "Counts as spend that went to a competitor." },
  COMPETITOR: { label: "Went to a competitor, for another reason",
                meaning: "Counts as spend that went to a competitor." },
  CUSTOMER_CANCELLED: { label: "The requirement went away — nobody supplied it",
                        meaning: "Counts as nobody's — no supplier gained this." },
  NO_DECISION: { label: "Still undecided, and gone quiet",
                 meaning: "Counted neither way; it may still move." },
  NOT_RECORDED: { label: "Reason not recorded", meaning: "Decided in the books." },
};

function payload() {
  return {
    currency: "INR", empty_reason: null,
    decided: 0, won: 0, open: 1, win_rate: null, min_decided_quotes: 6,
    won_value: 0, lost_value: 0, unpriced_quotes: 0, erp_decided_quotes: 0,
    sources_differ: false, reasons: [], owners: {}, reason_catalogue: CATALOGUE,
    customers: [], principals: [], product_lines: [], months: [], quotes: [],
    awaiting: [{
      quote_id: "Q-1", customer_id: "cst_1", customer_label: "Pitti Engineering",
      status: "SENT", sent_at: "2026-09-10T08:00:00Z", lines: 2, value: 120000,
      allowed_next: ["WON", "LOST"],
    }],
  };
}

async function choose(field: string, option: string) {
  fireEvent.mouseDown(screen.getByRole("combobox", { name: field }));
  fireEvent.click(await screen.findByRole("option", { name: option }));
}

function mount() {
  vi.spyOn(papi, "quoteOutcomes").mockResolvedValue(payload());
  vi.spyOn(papi, "listGroups").mockResolvedValue(
    { groups: [], kinds: [], may_edit: false, empty_reason: null } as never);
  return render(
    <QueryClientProvider client={new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })}>
      <MemoryRouter initialEntries={["/quote-outcomes"]}>
        <GroupScopeProvider token="t" ability={defineAbilityFor("SALESPERSON")}>
          <SnackbarProvider>
            <ThemeProvider theme={createTheme()}>
              <QuoteOutcomesScreen session={SESSION} />
            </ThemeProvider>
          </SnackbarProvider>
        </GroupScopeProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** Press Record outcome on the awaiting quote, say it went to Sandvik. */
async function recordLossToSandvik() {
  fireEvent.click(await screen.findByRole("button", { name: "Record outcome" }));
  const dialog = await screen.findByRole("dialog");
  await choose("Outcome", "Lost");
  await choose("Why we lost it", "Went to a competitor, for another reason");
  fireEvent.change(within(dialog).getByLabelText(/Who won it/), {
    target: { value: "Sandvik" },
  });
  fireEvent.click(within(dialog).getByRole("button", { name: "Record" }));
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("recording a loss on the Won & lost screen", () => {
  it("hands the writer who won it, as the dialog collected it", async () => {
    const outcome = vi.spyOn(intelligence, "outcome").mockResolvedValue({} as never);
    mount();
    await recordLossToSandvik();
    await waitFor(() => expect(outcome).toHaveBeenCalled());
    expect(outcome).toHaveBeenCalledWith(
      "t", "Q-1", "LOST", "Pitti Engineering", undefined, "COMPETITOR", "Sandvik");
  });

  it("puts lost_to on the wire, beside the reason", async () => {
    // Through the real client to the request body: the writer's parameter
    // order is the one thing the test above could get right by accident.
    const fetchSpy = vi.fn(async () => ({
      ok: true, status: 200,
      json: async () => ({ quote_id: "Q-1", status: "LOST" }),
      text: async () => "",
    }));
    vi.stubGlobal("fetch", fetchSpy);
    mount();
    await recordLossToSandvik();
    await waitFor(() => expect(fetchSpy).toHaveBeenCalled());
    const [url, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    const body = JSON.parse(String(init.body));
    expect(url).toBe("/api/v1/quote-intelligence/outcome");
    expect(body.loss_reason).toBe("COMPETITOR");
    expect(body.lost_to).toBe("Sandvik");
  });
});
