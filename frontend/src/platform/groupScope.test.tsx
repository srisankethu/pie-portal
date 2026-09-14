// One group selection per page, and the ways that could quietly not be true.
//
// The defect this replaced is the shape worth remembering: the Payments page
// drew two customer-group selects, because the settlements panel and the credit
// panel below it were separate components that each owned a `useGroupFilter`.
// Both looked right. Setting either left the other answering about the whole
// book, under a control that read as applied — so the test that matters most
// here is not "does the control work" but **"do two panels on one page see the
// same selection"**.
//
// The rest pins the decisions that are easy to undo by accident: the table is
// keyed by address rather than by screen, the selection lives in the URL, and a
// list that will not load leaves the page working.
//
// **The same defect has now been found three times and it is always a sibling
// panel.** Payments drew two selects; `/quote-outcomes` and `/quote-pricing`
// are one page and only the first was scoped; `/journey`, `/customers` and
// `/payables` each had a second panel fetching on its own. Adding a page to
// `SCOPED_BY` is therefore not the whole job — every panel the route renders
// has to read the scope or say why it does not, and the last of those is what
// `NotNarrowedByGroup` is for.

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { defineAbilityFor } from "./ability";
import {
  GroupScopeProvider, NotNarrowedByGroup, SCOPED_BY, useGroupScope,
} from "./groupScope";
import { PATH } from "./route";
import type { GroupKind, Role } from "./types";
import { aGroup } from "../test/groups";

const listGroups = vi.fn();
vi.mock("./api", () => ({ papi: { listGroups: (...a: unknown[]) => listGroups(...a) } }));

afterEach(() => listGroups.mockReset());

/** The server's answer for a kind, with one group in it. */
function oneGroup(kind: GroupKind, slug = "aerospace", name = "Aerospace") {
  return { groups: [aGroup({ entity_kind: kind, slug, name })],
           kinds: [], may_edit: true, empty_reason: null };
}

const empty = { groups: [], kinds: [], may_edit: true, empty_reason: null };

/** A panel that does nothing but report the scope it was handed. */
function Probe({ kind, name }: { kind: GroupKind; name: string }) {
  const slug = useGroupScope(kind);
  return <div data-testid={name}>{slug || "(whole book)"}</div>;
}

/** The address bar, so a test can assert what a selection wrote. */
function Address() {
  const { pathname, search } = useLocation();
  return <div data-testid="url">{pathname + search}</div>;
}

function mount(at: string, panels: ReactNodeLike, role: Role = "OWNER") {
  return render(
    <MemoryRouter initialEntries={[at]}>
      <GroupScopeProvider token="tok" ability={defineAbilityFor(role)}>
        <Address />
        {panels}
      </GroupScopeProvider>
    </MemoryRouter>,
  );
}
type ReactNodeLike = Parameters<typeof render>[0];

/** Open a MUI select and pick the option named. */
function pick(control: string, option: RegExp | string) {
  fireEvent.mouseDown(screen.getByRole("combobox", { name: control }));
  const list = screen.getByRole("listbox");
  fireEvent.click(within(list).getByRole("option", { name: option }));
}

describe("the declaration", () => {
  it("names addresses this application actually serves", () => {
    // Keyed by route, so a key is a `PATH` value and a typo is a page that
    // silently takes no group rather than a compile error.
    const served = new Set(Object.values(PATH));
    for (const key of Object.keys(SCOPED_BY)) expect(served).toContain(key);
  });

  it("scopes the customer directory and not one account", async () => {
    // The exact reason this table is keyed by address rather than by `Screen`:
    // `screenAt` calls `/customers` and `/account/x` both `customer`, correctly,
    // because they share a nav highlight — and only the first is a set of
    // accounts somebody narrows.
    expect(SCOPED_BY[PATH.customer]).toEqual(["CUSTOMER"]);
    expect(SCOPED_BY["/account/:id"]).toBeUndefined();

    listGroups.mockResolvedValue(oneGroup("CUSTOMER"));
    mount("/account/c-1", <div />);
    await waitFor(() => expect(screen.getByTestId("url")).toBeTruthy());
    expect(listGroups).not.toHaveBeenCalled();
    expect(screen.queryByRole("combobox")).toBeNull();
  });
});

describe("one selection, every panel on the page", () => {
  it("hands the same customer group to two panels", async () => {
    // The Payments defect, as an assertion. Settlements and credit are separate
    // components; there is one control and both read it.
    listGroups.mockResolvedValue(oneGroup("CUSTOMER"));
    mount(PATH.payments, <>
      <Probe kind="CUSTOMER" name="settlements" />
      <Probe kind="CUSTOMER" name="credit" />
    </>);

    await screen.findByRole("combobox", { name: "Customer group" });
    expect(screen.getAllByRole("combobox")).toHaveLength(1);

    pick("Customer group", /Aerospace/);

    expect(screen.getByTestId("settlements")).toHaveTextContent("aerospace");
    expect(screen.getByTestId("credit")).toHaveTextContent("aerospace");
  });

  it("asks the server for each kind once, however many panels read it", async () => {
    listGroups.mockResolvedValue(oneGroup("CUSTOMER"));
    mount(PATH.payments, <>
      <Probe kind="CUSTOMER" name="a" />
      <Probe kind="CUSTOMER" name="b" />
    </>);

    await screen.findByRole("combobox", { name: "Customer group" });
    expect(listGroups).toHaveBeenCalledTimes(1);
    expect(listGroups).toHaveBeenCalledWith("tok", "CUSTOMER");
  });

  it("draws one control per kind on the page that takes two", async () => {
    listGroups.mockImplementation((_t: string, kind: GroupKind) =>
      Promise.resolve(oneGroup(kind, kind === "CUSTOMER" ? "aerospace" : "drills",
                               kind === "CUSTOMER" ? "Aerospace" : "Drills")));
    mount(PATH.composition, <>
      <Probe kind="CUSTOMER" name="customers" />
      <Probe kind="PRODUCT" name="items" />
    </>);

    await screen.findByRole("combobox", { name: "Customer group" });
    await screen.findByRole("combobox", { name: "Item group" });

    pick("Item group", /Drills/);
    expect(screen.getByTestId("items")).toHaveTextContent("drills");
    // The other kind is untouched — two selections, not one shared slot.
    expect(screen.getByTestId("customers")).toHaveTextContent("whole book");
  });
});

describe("the URL is the selection", () => {
  it("reads a slug the address arrived with, before any control is drawn", async () => {
    // A link somebody sent. The panel must ask the server for the group's
    // figures on its first request, not fetch the whole book and correct itself
    // once the options land.
    listGroups.mockReturnValue(new Promise(() => {}));  // never settles
    mount(`${PATH.cadence}?customers=aerospace`, <Probe kind="CUSTOMER" name="cadence" />);

    expect(screen.getByTestId("cadence")).toHaveTextContent("aerospace");
  });

  it("writes the selection to the query", async () => {
    listGroups.mockResolvedValue(oneGroup("CUSTOMER"));
    mount(PATH.cadence, <Probe kind="CUSTOMER" name="cadence" />);
    await screen.findByRole("combobox", { name: "Customer group" });

    pick("Customer group", /Aerospace/);

    expect(screen.getByTestId("url"))
      .toHaveTextContent(`${PATH.cadence}?customers=aerospace`);
  });

  it("leaves a screen's own parameters alone", async () => {
    // `?item=` on Stock and `?scenario=` on the simulator belong to their
    // screens. Changing the scope is not a reason to drop them.
    listGroups.mockResolvedValue(oneGroup("PRODUCT", "drills", "Drills"));
    mount(`${PATH.catalogue}?sort=name`, <Probe kind="PRODUCT" name="items" />);
    await screen.findByRole("combobox", { name: "Item group" });

    pick("Item group", /Drills/);

    expect(screen.getByTestId("url")).toHaveTextContent("sort=name");
    expect(screen.getByTestId("url")).toHaveTextContent("items=drills");
  });

  it("clears every kind at once", async () => {
    listGroups.mockImplementation((_t: string, kind: GroupKind) =>
      Promise.resolve(oneGroup(kind)));
    mount(`${PATH.composition}?customers=aerospace&items=aerospace`, <>
      <Probe kind="CUSTOMER" name="customers" />
      <Probe kind="PRODUCT" name="items" />
    </>);
    await screen.findByRole("button", { name: /whole book/i });

    fireEvent.click(screen.getByRole("button", { name: /whole book/i }));

    expect(screen.getByTestId("customers")).toHaveTextContent("whole book");
    expect(screen.getByTestId("items")).toHaveTextContent("whole book");
  });

  it("offers nothing to clear when nothing is narrowed", async () => {
    listGroups.mockResolvedValue(oneGroup("CUSTOMER"));
    mount(PATH.cadence, <Probe kind="CUSTOMER" name="cadence" />);
    await screen.findByRole("combobox", { name: "Customer group" });

    expect(screen.queryByRole("button", { name: /whole book/i })).toBeNull();
  });
});

describe("when there is nothing to choose from", () => {
  it("draws no control at all", async () => {
    listGroups.mockResolvedValue(empty);
    mount(PATH.supply, <Probe kind="VENDOR" name="supply" />);

    await waitFor(() => expect(listGroups).toHaveBeenCalled());
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.getByTestId("supply")).toHaveTextContent("whole book");
  });

  it("leaves the page working when the list cannot be loaded", async () => {
    // The page behind this does not need the control. An error banner over a
    // working screen because an optional filter would not load is how people
    // learn to ignore banners.
    listGroups.mockRejectedValue(new Error("503"));
    mount(PATH.journey, <Probe kind="CUSTOMER" name="journey" />);

    await waitFor(() => expect(listGroups).toHaveBeenCalled());
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.getByTestId("journey")).toHaveTextContent("whole book");
  });
});

describe("a panel the scope deliberately does not reach", () => {
  // Two panels on `/payments` are whole-book by construction — the cash
  // projection reads both sides of the ledger, and self-funding is a question
  // about the legal entity. Both are correct and both look like the filter is
  // broken, which is the whole reason this renders anything at all.
  it("says nothing at all while the page is the whole book", async () => {
    listGroups.mockResolvedValue(oneGroup("CUSTOMER"));
    const { container } = mount(
      PATH.payments, <NotNarrowedByGroup kind="CUSTOMER" why="Because." />);

    await screen.findByRole("combobox", { name: "Customer group" });
    expect(container.textContent).not.toContain("Because.");
  });

  it("says so once a group is selected", async () => {
    listGroups.mockResolvedValue(oneGroup("CUSTOMER"));
    mount(`${PATH.payments}?customers=aerospace`,
          <NotNarrowedByGroup kind="CUSTOMER" why="Both sides of the ledger." />);

    expect(await screen.findByText(/Both sides of the ledger\./)).toBeTruthy();
    // Not "Whole book" — that is the bar's clear button, and the two saying the
    // same words on one screen is the collision this wording avoids.
    expect(screen.getByText("Not narrowed")).toBeTruthy();
    expect(screen.getByRole("button", { name: /whole book/i })).toBeTruthy();
  });

  it("stays quiet for a kind this page does not offer", async () => {
    // A vendor note on a customer-only page would be an explanation of
    // something nobody is doing.
    listGroups.mockResolvedValue(oneGroup("CUSTOMER"));
    const { container } = mount(
      `${PATH.payments}?customers=aerospace&vendors=aerospace`,
      <NotNarrowedByGroup kind="VENDOR" why="Not applicable here." />);

    await screen.findByRole("combobox", { name: "Customer group" });
    expect(container.textContent).not.toContain("Not applicable here.");
  });
});


describe("a kind this reader cannot use", () => {
  // `/bonds` answers about both ends of the book in one response and the server
  // omits the supplier end for a salesperson. A "Vendor group" select on their
  // screen would change nothing they could see — the same defect as a tab that
  // always 403s, which this product removes rather than renders.
  it("is not drawn for a salesperson", async () => {
    listGroups.mockImplementation((_t: string, kind: GroupKind) =>
      Promise.resolve(oneGroup(kind)));
    mount(PATH.bonds, <>
      <Probe kind="CUSTOMER" name="customers" />
      <Probe kind="VENDOR" name="vendors" />
    </>, "SALESPERSON");

    await screen.findByRole("combobox", { name: "Customer group" });
    expect(screen.queryByRole("combobox", { name: "Vendor group" })).toBeNull();
    expect(screen.getAllByRole("combobox")).toHaveLength(1);
  });

  it("is drawn for a manager, who has the half it narrows", async () => {
    listGroups.mockImplementation((_t: string, kind: GroupKind) =>
      Promise.resolve(oneGroup(kind)));
    mount(PATH.bonds, <Probe kind="VENDOR" name="vendors" />, "SALES_MANAGER");

    expect(await screen.findByRole("combobox", { name: "Vendor group" }))
      .toBeTruthy();
  });

  it("is not fetched for a salesperson either", async () => {
    // Not merely hidden: a list nobody can act on is a request nobody needs.
    listGroups.mockImplementation((_t: string, kind: GroupKind) =>
      Promise.resolve(oneGroup(kind)));
    mount(PATH.bonds, <Probe kind="CUSTOMER" name="customers" />, "SALESPERSON");

    await screen.findByRole("combobox", { name: "Customer group" });
    expect(listGroups.mock.calls.map((c: unknown[]) => c[1])).toEqual(["CUSTOMER"]);
  });

  it("answers the whole book without complaining about the wiring", async () => {
    // The distinction this pins: a declared-but-withheld kind is correct code
    // on a screen whose response has no such half, so it is silent. Only an
    // undeclared kind is a wiring mistake worth saying out loud.
    const complained = vi.spyOn(console, "error").mockImplementation(() => {});
    listGroups.mockImplementation((_t: string, kind: GroupKind) =>
      Promise.resolve(oneGroup(kind)));

    mount(`${PATH.bonds}?vendors=aerospace`,
          <Probe kind="VENDOR" name="vendors" />, "SALESPERSON");

    expect(screen.getByTestId("vendors")).toHaveTextContent("whole book");
    expect(complained).not.toHaveBeenCalled();
    complained.mockRestore();
  });
});


describe("a kind the page does not declare", () => {
  it("answers the whole book, and says so to whoever wired it", async () => {
    // A panel asking for a scope with no control on screen to set it is a
    // wiring mistake. The honest behaviour is the unscoped answer it would have
    // given anyway — said out loud rather than silently narrowing on a
    // parameter nobody can clear.
    const complained = vi.spyOn(console, "error").mockImplementation(() => {});
    listGroups.mockResolvedValue(oneGroup("CUSTOMER"));

    mount(`${PATH.supply}?customers=aerospace`, <Probe kind="CUSTOMER" name="stray" />);

    expect(screen.getByTestId("stray")).toHaveTextContent("whole book");
    expect(complained).toHaveBeenCalled();
    complained.mockRestore();
  });

  it("refuses to be read outside a provider", () => {
    const quiet = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<Probe kind="CUSTOMER" name="orphan" />)).toThrow(/GroupScopeProvider/);
    quiet.mockRestore();
  });
});
