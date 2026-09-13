// Choosing an item by hand, when the engine could not choose one.
//
// The backend half is pinned in `backend/tests/test_item_search.py` — what the
// two searches find, what they refuse, and that a hand-picked code can never
// become an asserted identity. This file pins the half those cannot reach:
// what the person on the other side of the screen is actually told.
//
// The sharpest of those is the third test. An engine that is not installed and
// a catalogue that genuinely does not carry the part produce the same empty
// list, and if the screen renders both as "nothing found" it has told somebody
// something false about their product. CLAUDE.md §1: absence of evidence is
// not a pass.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ItemSearch } from "./ItemSearch";
import type { ItemSearch as Results } from "../types";

const searchItems = vi.fn();

vi.mock("../api", () => ({
  api: { searchItems: (...a: unknown[]) => searchItems(...a) },
}));

function results(over: Partial<Results> = {}): Results {
  return {
    query: "CNMG120408",
    catalogue: { records: [], available: true, reason: null, searched: 6717 },
    books: { records: [], searched: 0 },
    ...over,
  };
}

function catalogueRecord(over = {}) {
  return {
    code: "2576285", desc: "CNMG 120408 - TN2000", grade: "TN2000",
    brand: "WIDIA", catalogue: "widia", similarity: 0.81, attributes: {},
    ...over,
  };
}

function bookItem(over = {}) {
  return {
    code: "CNMG120408-UC-D2 YC0014", name: "CNMG120408-UC-D2 YC0014",
    externalId: "z-1", manufacturer: "WIDIA", uom: "pcs", active: true,
    ...over,
  };
}

function draw(onSelect = vi.fn(), readOnly = false) {
  render(<ItemSearch quoteId="q-1" token="t" readOnly={readOnly} onSelect={onSelect} />);
  return onSelect;
}

/** `fireEvent`, not `user-event` — the latter is not a dependency of this
 *  repo and a test is not the place to add one. One change event is also
 *  closer to what the debounce actually sees than a keystroke per character. */
function type(text: string) {
  fireEvent.change(screen.getByLabelText(/search for an item/i),
                   { target: { value: text } });
}

describe("ItemSearch", () => {
  beforeEach(() => {
    searchItems.mockReset();
    vi.useRealTimers();
  });

  it("searches both the catalogue and the item master", async () => {
    searchItems.mockResolvedValue(results({
      catalogue: { records: [catalogueRecord()], available: true, reason: null, searched: 6717 },
      books: { records: [bookItem()], searched: 1 },
    }));
    draw();
    type("CNMG120408");
    expect(await screen.findByText("2576285")).toBeTruthy();
    expect(screen.getByText("CNMG120408-UC-D2 YC0014")).toBeTruthy();
    // The two are labelled, because "the maker lists it" and "we already sell
    // it" are different facts and lead to different next steps.
    expect(screen.getByText(/in the catalogue/i)).toBeTruthy();
    expect(screen.getByText(/in your item master/i)).toBeTruthy();
  });

  it("selects a code as a manual pick", async () => {
    searchItems.mockResolvedValue(results({
      books: { records: [bookItem()], searched: 1 },
    }));
    const onSelect = draw();
    type("CNMG120408");
    await screen.findByText("CNMG120408-UC-D2 YC0014");
    fireEvent.click(screen.getByTitle(/Set CNMG120408-UC-D2 YC0014 as the supply/i));
    // `manual` true, always: nothing in this list was ranked against the
    // request, so the line must not record it as the engine's choice.
    expect(onSelect).toHaveBeenCalledWith("CNMG120408-UC-D2 YC0014", true);
  });

  it("says the catalogue could not be searched, rather than showing nothing", async () => {
    const reason = "The resolution engine is not installed on this deployment, "
      + "so the catalogue could not be searched.";
    searchItems.mockResolvedValue(results({
      catalogue: { records: [], available: false, reason, searched: 0 },
    }));
    draw();
    type("CNMG120408");
    expect(await screen.findByText(reason)).toBeTruthy();
    // And never the sentence that would be a lie in this state.
    expect(screen.queryByText(/reads like that/i)).toBeNull();
  });

  it("reports a real miss as a miss, with what it searched", async () => {
    searchItems.mockResolvedValue(results());
    draw();
    type("CNMG120408");
    // Searched and found nothing IS evidence, so it is stated as evidence —
    // beside the size of the thing that produced it.
    expect(await screen.findByText(/reads like that/i)).toBeTruthy();
    expect(screen.getByText(/6,717 records searched/)).toBeTruthy();
  });

  it("marks an item the ledger has deactivated", async () => {
    searchItems.mockResolvedValue(results({
      books: { records: [bookItem({ active: false })], searched: 1 },
    }));
    draw();
    type("CNMG120408");
    // Shown rather than hidden: a person looking for it needs to know it is
    // there and why it will not go on a document.
    expect(await screen.findByText("inactive")).toBeTruthy();
  });

  it("does not search until enough has been typed", async () => {
    searchItems.mockResolvedValue(results());
    draw();
    type("C");
    await new Promise((r) => setTimeout(r, 400));
    expect(searchItems).not.toHaveBeenCalled();
  });

  it("offers no way to change a quote the reader may not change", async () => {
    searchItems.mockResolvedValue(results({
      books: { records: [bookItem()], searched: 1 },
    }));
    draw(vi.fn(), true);
    type("CNMG120408");
    await screen.findByText("CNMG120408-UC-D2 YC0014");
    expect(screen.queryByTitle(/as the supply product/i)).toBeNull();
  });

  it("says the search failed rather than reporting an empty catalogue", async () => {
    searchItems.mockRejectedValue(new Error("503 service unavailable"));
    draw();
    type("CNMG120408");
    await waitFor(() => expect(screen.getByText(/did not run/i)).toBeTruthy());
    expect(screen.queryByText(/reads like that/i)).toBeNull();
  });
});
