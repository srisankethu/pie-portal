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
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

function session(role: Role): PlatformSession {
  return {
    token: "tok", role, name: "Test", user_id: "u1", organization_id: "org1",
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

function mount(role: Role = "OWNER") {
  return render(
    <MemoryRouter>
      <CustomerPicker
        open
        session={session(role)}
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

  it("asks the second question only when the first came back empty", async () => {
    listAccounts.mockResolvedValue([account("Pitti Engineering")]);
    mount("OWNER");

    await waitFor(() => expect(listAccounts).toHaveBeenCalledTimes(1));
    // A directory with rows in it must not pay for a probe whose answer it
    // already has.
    expect(listAccounts).toHaveBeenCalledWith("tok", "");
  });
});
