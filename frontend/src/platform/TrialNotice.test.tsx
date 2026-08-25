// When the trial notice speaks, and what it says when it does.
//
// The server side of this is pinned in `test_entitlements.py`: the countdown,
// the timezone it is measured in, and the list of what expiry costs. What is
// left here is the part that is genuinely a *product* decision rather than
// arithmetic — when a banner is worth interrupting somebody for, who is shown
// it, and whether it promises anything the deployment cannot do.
//
// That last one is the reason this file exists rather than being left to
// judgement. There is still no billing in this product: `set_plan` is an
// operator command with deliberately no API. What the notice grew is not a
// checkout — it records a request that a person then decides — and the tests
// below hold it to being exactly that. A control here that implied a payment,
// or that claimed the plan had moved, would be a lie shipped to the one person
// most likely to press it, and nothing else in the suite would notice.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { TrialNotice } from "./TrialNotice";
import type { Entitlements, PlatformSession, Role } from "./types";

const { entitlements, requestPlan } = vi.hoisted(
  () => ({ entitlements: vi.fn(), requestPlan: vi.fn() }));
vi.mock("./api", () => ({ papi: { entitlements, requestPlan } }));

function session(role: Role = "OWNER"): PlatformSession {
  return {
    token: "t", role, name: "O", user_id: "u1", organization_id: "org_x",
    currency: "INR", timezone: "Asia/Kolkata",
  };
}

function view(days: number | null): Entitlements {
  return {
    plan: "free",
    plan_label: "Quote Desk (no subscription)",
    effective_plan: days === null ? "free" : "intelligence",
    effective_label: "Commercial Intelligence",
    status: days === null ? "EXPIRED" : "TRIALING",
    trial: days === null
      ? null
      : { active: true, started_at: "2026-08-02T00:00:00Z",
          ends_at: "2026-09-01T00:00:00Z", ends_on: "2026-09-01",
          days_remaining: days, ended_reason: "" },
    features: { intelligence: days !== null, multi_company: false },
    loses_on_expiry: days === null ? [] : ["intelligence"],
    locked: days === null ? ["intelligence", "multi_company"] : ["multi_company"],
    pending_request: null,
  };
}

/** An organization whose trial has run out. Distinct from `view(null)`, which
 *  is "never had one" — and the distinction is the point: the two used to be
 *  the same payload, so the notice could not say "ended on the 3rd" and went
 *  silent on the day the decision layer disappeared. */
function ended(reason = ""): Entitlements {
  return {
    ...view(null),
    trial: { active: false, started_at: "2026-07-02T00:00:00Z",
             ends_at: "2026-08-01T00:00:00Z", ends_on: "2026-08-01",
             days_remaining: 0, ended_reason: reason },
  };
}

/** The same view, after the owner has asked. */
function asked(days: number): Entitlements {
  return {
    ...view(days),
    pending_request: {
      request_id: "req_1",
      requested_plan: "intelligence",
      requested_plan_label: "Commercial Intelligence",
      requested_at: "2026-08-18T09:00:00Z",
      status: "REQUESTED",
    },
  };
}

/** Mounted with its own client so one test's cache cannot answer the next. */
async function show(v: Entitlements, role: Role = "OWNER") {
  entitlements.mockClear();
  entitlements.mockResolvedValue(v);
  // A fresh client per render is the whole isolation story. Deliberately no
  // `gcTime: 0`: a query whose observers are mid-transition can then be
  // collected between resolving and rendering, and the component reads
  // `undefined`.
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <TrialNotice session={session(role)} />
    </QueryClientProvider>,
  );
  // Wait for the *query* to reach success, not for the mock to have been called.
  // Half these tests assert an absence, and an absence is only meaningful once
  // the data has arrived and the component has decided to render nothing —
  // "not there yet" and "deliberately not rendered" are the same empty DOM.
  //
  // Two weaker versions of this were flaky before it, in the way that matters:
  // exactly one test failed per run and a different one each time. Waiting on
  // `entitlements` having been called returns on the *previous* test's call
  // unless the mock is cleared, and even cleared it returns before the promise
  // has propagated into a render.
  await waitFor(() =>
    expect(client.getQueryCache().find({ queryKey: ["entitlements", "org_x"] })
      ?.state.status).toBe("success"));
}

describe("TrialNotice", () => {
  it("says nothing while the trial has plenty left", async () => {
    await show(view(20));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("speaks inside the last ten days", async () => {
    await show(view(10));
    expect(screen.getByRole("alert")).toHaveTextContent(/ends in 10 days/i);
  });

  it("names what is actually lost, from the server's list", async () => {
    await show(view(5));
    expect(screen.getByRole("alert")).toHaveTextContent(
      /decision queue and the insight screens/i);
  });

  it("promises the quote desk keeps working, because it does", async () => {
    await show(view(5));
    expect(screen.getByRole("alert")).toHaveTextContent(/quoting, margin\s+floors and approvals carry on/i);
    expect(screen.getByRole("alert")).toHaveTextContent(/nothing you have synced\s+is deleted/i);
  });

  it("offers an ask, and does not dress it as a purchase", async () => {
    // This test used to assert the opposite — no control at all — because a
    // button that opened a checkout nobody built would have been worse than
    // none. What changed is that the control no longer implies one: it records
    // a request. The wording is the whole of the difference, so the wording is
    // what is pinned.
    await show(view(1));
    const button = screen.getByRole("button");
    expect(button).toHaveTextContent(/ask to keep/i);
    expect(button).not.toHaveTextContent(/upgrade|buy|pay|checkout|card/i);
  });

  it("shows a salesperson nothing to press", async () => {
    // The whole notice is already owner-and-manager only; this pins that a
    // *manager* — who can read it — is not offered a commitment to spend.
    await show(view(1), "SALES_MANAGER");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/your owner can arrange/i);
  });

  it("replaces the control with what was asked, once it has been", async () => {
    // An owner who pressed it and still sees a button concludes it did not
    // work and presses again — which the server refuses, so the screen would
    // be inviting an error it created.
    await show(asked(1));
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/you asked to move to/i);
    expect(screen.getByRole("alert")).toHaveTextContent(/Commercial Intelligence/);
  });

  it("never claims the plan has moved", async () => {
    // The one thing this screen must not say. Asking is not granting, and a
    // notice that implied otherwise would contradict the entitlement the very
    // same payload carries.
    await show(asked(1));
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/nothing is charged from these screens/i);
    expect(alert).not.toHaveTextContent(/you are now on|upgraded|activated/i);
  });

  // Two tests rather than two renders in one: `cleanup` runs between tests, not
  // between renders, so a second `render` in the same test leaves the first
  // component mounted and `getByRole` then answers from the wrong one. That is
  // precisely the failure `src/test/setup.ts` warns about, reached from the
  // other side.
  it("reads as merely informative with a week to go", async () => {
    await show(view(7));
    expect(screen.getByRole("alert").className).toMatch(/Info/);
  });

  it("reads as urgent inside the last three days", async () => {
    await show(view(2));
    expect(screen.getByRole("alert").className).toMatch(/Warning/);
  });

  it("counts the last day in words rather than as 0 days", async () => {
    await show(view(0));
    expect(screen.getByRole("alert")).toHaveTextContent(/ends today/i);
  });

  it("stays silent for a salesperson, who has no lever to pull", async () => {
    await show(view(1), "SALESPERSON");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("disappears when there has never been a trial", async () => {
    await show(view(null));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  // ── after the trial ────────────────────────────────────────────────────────
  //
  // The case this component was written for and did not cover. It returned
  // null the moment the trial did, so the decision layer vanished overnight
  // with nothing on screen saying why — which reads as a fault, and sends
  // somebody to support instead of to the plan.
  it("keeps speaking once the trial has ended", async () => {
    await show(ended());
    expect(screen.getByRole("alert")).toHaveTextContent(/trial ended on 2026-08-01/i);
  });

  it("says the data is untouched, which is the first thing an owner needs", async () => {
    await show(ended());
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/exactly where you left it/i);
    expect(alert).toHaveTextContent(/nothing was deleted/i);
  });

  it("names what is locked from the server's list, not a copy of the plan map", async () => {
    await show(ended());
    expect(screen.getByRole("alert")).toHaveTextContent(
      /decision queue and the insight screens/i);
  });

  it("gives the reason when something ended the trial early", async () => {
    // The books-already-trialled case. A decision layer that switches off on
    // the day somebody connects their books reads as a fault unless the screen
    // says otherwise, and the sentence is the server's — written to be read.
    await show(ended("These books have already had their trial."));
    expect(screen.getByRole("alert")).toHaveTextContent(
      /these books have already had their trial/i);
  });

  it("offers the ask again, worded for an organization that has none", async () => {
    await show(ended());
    expect(screen.getByRole("button")).toHaveTextContent(/ask for commercial intelligence/i);
  });

  it("reads as urgent once it has ended", async () => {
    await show(ended());
    expect(screen.getByRole("alert").className).toMatch(/Warning/);
  });

  it("still says nothing to a salesperson after expiry", async () => {
    await show(ended(), "SALESPERSON");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
