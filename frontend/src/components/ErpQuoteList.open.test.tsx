// Opening a quote from the list.
//
// The list's job here is one thing: pressing a row goes to that quote's page.
// It used to fetch the lines itself and hand them to a drawer, and the fetch
// between the two suites — the only part that could hang — was tested by
// neither, which is how the panel came to sit on a loading skeleton for ever in
// the real app. That fetch is `ErpQuoteScreen`'s now and is tested there.
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ErpQuoteList } from "./ErpQuoteList";
import type { ErpQuote } from "../types";
import { pretendViewportIs } from "../test/viewport";

const navigate = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>(
    "react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

function q(over: Partial<ErpQuote> = {}): ErpQuote {
  return {
    quote_document_ref: "2263307000011272461",
    number: "QT FY27-013",
    customer_id: "c1",
    customer_label: "M/s. PITTI ENGINEERING LIMITED",
    source_status: "invoiced",
    outcome: "WON",
    raised_on: "2026-07-23",
    expires_on: null,
    decided_on: "2026-07-24",
    value: 101139,
    opened_at: null,
    company: "SLS Engineers",
    attributes: {},
    ...over,
  };
}

function draw(quotes: ErpQuote[]) {
  pretendViewportIs(412);
  return render(
    <MemoryRouter>
      <ErpQuoteList quotes={quotes} emptyReason={null} />
    </MemoryRouter>);
}

beforeEach(() => { navigate.mockReset(); });
afterEach(() => { vi.unstubAllGlobals(); });

describe("a quote this platform wrote", () => {
  it("says which draft it came from, and still opens like the rest", () => {
    // The same document used to be two unrelated rows on two tabs — a "Sent"
    // draft here and an ERP quote there — with nothing saying they were one.
    draw([q({ platform_quote: { quote_id: "q42", number: "QB-0042" } })]);

    expect(screen.getByText(/built in PIE as QB-0042/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("QT FY27-013"));
    expect(navigate).toHaveBeenCalledWith("/quotes/erp/2263307000011272461");
  });

  it("names the book only when asked to", () => {
    draw([q()]);
    expect(screen.queryByText(/SLS Engineers/)).not.toBeInTheDocument();
  });
});

describe("pressing a quote", () => {
  it("goes to that quote's page", () => {
    // The report this came from: the rows were on screen and none of them did
    // anything when pressed.
    draw([q()]);

    fireEvent.click(screen.getByText("QT FY27-013"));

    expect(navigate).toHaveBeenCalledWith("/quotes/erp/2263307000011272461");
  });

  it("goes to the one that was pressed", () => {
    draw([q(), q({ quote_document_ref: "ref-b", number: "QT FY27-014" })]);

    fireEvent.click(screen.getByText("QT FY27-014"));

    expect(navigate).toHaveBeenCalledWith("/quotes/erp/ref-b");
  });

  it("gives every row a control a keyboard can reach", () => {
    // A row somebody can see and cannot open is a row a screen-reader user
    // cannot read at all.
    draw([q()]);

    expect(screen.getByRole("button", { name: /QT FY27-013/ })).toBeTruthy();
  });
});
