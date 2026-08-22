// What the customer picker says when it has nothing to offer.
//
// This dialog is the only way into the Quote Builder — no customer, no quote —
// so its empty state is not decoration. It used to answer "Start typing a name."
// to four different facts: the request failed, the organization has never
// synced, every customer on file is dormant, and a salesperson holds no
// accounts. Only one of those is fixed by typing, and the deployment that found
// this had the second one: a picker with an empty list, a disabled button and no
// cancel, over a backdrop that swallows the nav.
//
// So what is pinned here is the wording, per reason. A test that only asserted
// "some message appears" would have passed against the bug it was written for.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CustomerPicker } from "./CustomerPicker";
import type { Account, PlatformSession, Role } from "../platform/types";

const listAccounts = vi.fn();
vi.mock("../platform/api", () => ({ papi: { listAccounts: (...a: unknown[]) => listAccounts(...a) } }));

afterEach(() => {
  listAccounts.mockReset();
  vi.useRealTimers();
});

function session(role: Role, token = "tok"): PlatformSession {
  return {
    token, role, name: "Test", user_id: "u1", organization_id: "org1",
    currency: "INR", timezone: "Asia/Kolkata",
  };
}

function account(name: string): Account {
  return {
    customer_id: "c1", name, status: "ACTIVE",
    assigned_user_id: null, last_order: null, orders_12m: 0, revenue_12m: 0,
    origin: undefined, sources_differ: false,
  } as Account;
}

function mount(role: Role = "OWNER", token?: string) {
  return render(
    <MemoryRouter>
      <CustomerPicker
        open
        session={session(role, token)}
        title="Who is this quote for?"
        onPick={() => {}}
      />
    </MemoryRouter>,
  );
}

describe("CustomerPicker, with nothing to show", () => {
  it("names a sync as what fills an empty directory, and offers the way there", async () => {
    // Both calls empty: the active list, and the "is anything on file at all"
    // question behind it.
    listAccounts.mockResolvedValue([]);
    mount("OWNER");

    expect(await screen.findByText("No customers have been synced yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Data & connection/ })).toBeInTheDocument();
    // The old sentence, which was true of none of these cases.
    expect(screen.queryByText("Start typing a name.")).toBeNull();
    // And no field to type it into: searching an empty directory returns
    // nothing however carefully it is spelled.
    expect(screen.getByLabelText("Customer")).not.toBeVisible();
  });

  it("does not tell a salesperson to go and sync — they cannot", async () => {
    listAccounts.mockResolvedValue([]);
    mount("SALESPERSON");

    expect(await screen.findByText("No accounts are assigned to you yet")).toBeInTheDocument();
    // `routers/accounts` scopes them to their own accounts and the connection
    // screen is manager-or-owner, so the button would open a screen that
    // refuses them. Naming who to ask is the honest answer.
    expect(screen.queryByRole("button", { name: /Data & connection/ })).toBeNull();
    expect(screen.getByText(/Ask an owner to assign you one/)).toBeInTheDocument();
  });

  it("distinguishes a dormant directory from an unsynced one", async () => {
    // Nothing active, but rows on file: a sync has run and would not help.
    listAccounts
      .mockResolvedValueOnce([])                    // status=active
      .mockResolvedValueOnce([account("Dormant Co")]); // status=all
    mount("OWNER");

    expect(await screen.findByText("Every customer on file is inactive")).toBeInTheDocument();
    expect(screen.queryByText("No customers have been synced yet")).toBeNull();
  });

  it("surfaces a failed load on the dialog, with a way to retry", async () => {
    listAccounts.mockRejectedValue(new Error("401 unauthorized"));
    mount("OWNER");

    expect(await screen.findByText("Customers could not be loaded")).toBeInTheDocument();
    expect(screen.getByText(/401 unauthorized/)).toBeInTheDocument();

    // A failure used to be terminal: the load re-runs on the query, and the one
    // person who cannot usefully type is the one whose list would not load.
    listAccounts.mockReset();
    listAccounts.mockResolvedValue([account("Pitti Engineering")]);
    fireEvent.click(screen.getByRole("button", { name: /Try again/ }));

    await waitFor(() => expect(screen.queryByText("Customers could not be loaded")).toBeNull());
  });

  it("says nothing when there are customers to choose from", async () => {
    listAccounts.mockResolvedValue([account("Pitti Engineering")]);
    mount("OWNER");

    await waitFor(() => expect(listAccounts).toHaveBeenCalled());
    expect(screen.queryByText("No customers have been synced yet")).toBeNull();
    expect(screen.queryByText("Customers could not be loaded")).toBeNull();
    // The search field is the whole dialog when the directory has rows in it.
    expect(screen.getByLabelText("Customer")).toBeVisible();
  });

  it("loads the directory on a cookie session, where the token is empty", async () => {
    // The state every browser is actually in after a reload. The session moved
    // into an httpOnly cookie, so `savePlatformSession` stores `token: ""` and
    // `/auth/me` restores it that way — and this dialog guarded its fetch on the
    // token being truthy, so it never asked. Every name typed into it came back
    // "No customer matches that.", because `rows` was empty and always would be.
    //
    // Every test above passes a token, which is why the bug survived them: the
    // one session shape that reaches production was the one never mounted.
    listAccounts.mockResolvedValue([account("Pitti Engineering")]);
    mount("OWNER", "");

    await waitFor(() => expect(listAccounts).toHaveBeenCalledWith("", ""));
    expect(screen.getByLabelText("Customer")).toBeVisible();
    // Not the empty-directory panel: an unasked question is not an empty answer.
    expect(screen.queryByText("No customers have been synced yet")).toBeNull();
  });

  it("never offers the previous search's names under the new one", async () => {
    // The reported bug, and the reason this asserts on the *window* rather than
    // on the settled result: search runs on the server, so between the keystroke
    // and the response there is a debounce plus a round trip in which `rows`
    // still holds the last query's answer. The dialog rendered it. Typing
    // "pitti" showed 7TH GEAR, ADVENT and ARUSH — three names containing no
    // "p" — sitting in the listbox looking exactly like a result, with a 16px
    // spinner as the only disclaimer. A test that waited for the answer would
    // have passed against that, which is why this one looks while it is wrong.
    const ALL = [account("7TH GEAR AUTOMOTIVE LLP"), account("ADVENT TECHNOLOGIES")];
    let release: (() => void) | null = null;
    listAccounts.mockImplementation((_t: string, q: string) => {
      if (!q) return Promise.resolve(ALL);
      // Held open, standing in for the round trip the screenshot caught.
      return new Promise((resolve) => {
        release = () => resolve([account("PITTI ENGINEERING LTD")]);
      });
    });
    mount("OWNER", "");

    const field = screen.getByLabelText("Customer");
    await waitFor(() => expect(listAccounts).toHaveBeenCalled());
    fireEvent.focus(field);
    // Focus first: MUI resets an unfocused Autocomplete's input on the next
    // render, so a change event without it never reaches `onInputChange`.
    fireEvent.change(field, { target: { value: "pitti" } });

    // While the answer is outstanding the dialog says so, and offers nothing.
    expect(await screen.findByText("Searching the directory…")).toBeInTheDocument();
    expect(screen.queryByText("7TH GEAR AUTOMOTIVE LLP")).toBeNull();
    expect(screen.queryByText("ADVENT TECHNOLOGIES")).toBeNull();
    // Nor the opposite wrong answer: "no match" is also a claim about a
    // directory that has not replied yet.
    expect(screen.queryByText("No customer matches that.")).toBeNull();

    await waitFor(() => expect(release).not.toBeNull());
    release!();

    const box = await screen.findByRole("listbox");
    await waitFor(() => expect(
      within(box).getAllByRole("option").map((o) => o.textContent),
    ).toEqual(["PITTI ENGINEERING LTD"]));
  });

  it("asks the second question only when the first came back empty", async () => {
    listAccounts.mockResolvedValue([account("Pitti Engineering")]);
    mount("OWNER");

    await waitFor(() => expect(listAccounts).toHaveBeenCalledTimes(1));
    // A directory with rows in it must not pay for a probe whose answer it
    // already has.
    expect(listAccounts).toHaveBeenCalledWith("tok", "");
  });
});
