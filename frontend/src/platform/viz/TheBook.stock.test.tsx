// "Nothing here. That is the good answer." — the sentence this file exists for.
//
// The shelf screen renders one line under any group with no rows in it, and it
// used to render *that* line under every one of them. On this book the reorder
// group is empty because `08-intermittent-demand.md` measured 0 of 3,673 items
// with a reorder level set — so the group cannot contain a row, and the screen
// read a structurally-impossible zero back as a clean bill of health. That is
// `CLAUDE.md` §1's *absence of evidence is not a pass*, rendered in words.
//
// The server decides, because only the server can: it holds the population
// behind the group, and the client holds only the group. `empty_means` is that
// decision. These two tests are the fork.
//
// **Rendered narrow on purpose**, the same reason `UnrecordedQuotes.test.tsx`
// gives: below `NARROW_BREAKPOINT` the ag-grid chunk is never fetched, so these
// assertions are about this component rather than about ag-grid inside jsdom.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";

import { papi } from "../api";
import { StockScreen } from "./TheBook";
import type { PlatformSession } from "../types";

vi.mock("../api", () => ({ papi: { stock: vi.fn() } }));

const SESSION: PlatformSession = {
  token: "t", role: "OWNER", name: "S. Uppalapati", user_id: "u1",
  organization_id: "org_pie", currency: "INR", timezone: "Asia/Kolkata",
};

function group(over: Record<string, unknown> = {}) {
  return {
    key: "BELOW_REORDER",
    label: "At or below the reorder point",
    meaning: "Only for items where a reorder point has actually been set.",
    items: [],
    count: 0,
    empty_means: null,
    ...over,
  };
}

function envelope(over: Record<string, unknown> = {}) {
  return {
    as_of: "2026-08-10",
    groups: [group()],
    counts: {},
    items: [],
    kpis: [],
    filters: [],
    unavailable: [],
    ...over,
  };
}

function draw(body: Record<string, unknown>) {
  vi.mocked(papi.stock).mockResolvedValue(body as never);
  return render(
    <QueryClientProvider client={new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })}>
      <MemoryRouter>
        <StockScreen session={SESSION} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  window.matchMedia = window.matchMedia || ((q: string) => ({
    matches: false, media: q, onchange: null,
    addListener: () => {}, removeListener: () => {},
    addEventListener: () => {}, removeEventListener: () => {},
    dispatchEvent: () => false,
  } as unknown as MediaQueryList));
});

it("does not congratulate anyone on a group that could not have held a row", async () => {
  const why = "Empty because no item has a reorder point at all — 0 of 3 carry "
    + "one in Zoho.";
  draw(envelope({ groups: [group({ empty_means: why })] }));

  expect(await screen.findByText(why)).toBeTruthy();
  expect(screen.queryByText(/That is the good answer/)).toBeNull();
});

it("still says an empty group is good news when it genuinely is", async () => {
  // The edge that matters most: a book that has set its reorder points and has
  // nothing below them deserves the reassurance, and a screen that cried wolf
  // here would be worse than the one this change replaced.
  draw(envelope());

  await waitFor(() => expect(
    screen.getByText(/That is the good answer/)).toBeTruthy());
});
