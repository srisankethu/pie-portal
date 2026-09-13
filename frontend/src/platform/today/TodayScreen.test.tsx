/** Working the queue: open on the first thing, act, advance.
 *
 * The screen this replaced could be read and could not be worked — four clicks
 * from the landing page to an action, and no way back to your place. So what is
 * pinned here is the working loop itself: the hardest item is already open with
 * its arithmetic on screen, acting records it and moves on, and what you have
 * answered stays visible so the morning has a shape.
 *
 * The last test is the honest one. The design this came from offers undo on
 * everything; an approval decision and a recorded outcome are append-only on
 * the server and there is no un-decide, so those say "recorded" where a
 * decision — which `reopen` really does take back — offers the button.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { papi } from "../api";
import type { ApprovalRequest, DecisionDetail, PlatformSession } from "../types";
import TodayScreen from "./TodayScreen";

// jsdom has no `ResizeObserver`, and the rail measures the panel beside it to
// decide how tall it is allowed to be. Stubbed rather than given real numbers:
// jsdom has no layout either, so every box is 0×0 and the rail falls back to
// its viewport cap — which is the branch these tests exercise. What the cap
// *is* on a real screen is a browser question, not a jsdom one.
beforeAll(() => {
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = RO;
});

const SESSION: PlatformSession = {
  token: "t", role: "OWNER", name: "S. Menon", user_id: "u1",
  organization_id: "org_pie", currency: "INR", timezone: "Asia/Kolkata",
};

const APPROVAL: ApprovalRequest = {
  approval_request_id: "ap1", kind: "QUOTE_LINE_PRICE", status: "PENDING",
  required_authority: "OWNER", subject_id: "q1", subject_line_id: "l1",
  title: "TNMG160404 at ₹298", summary: "Below landed cost on 400 pieces.",
  reason: "Matching a rate the customer showed him", reason_code: null,
  requested_by: "Ravi K", requested_by_user_id: "u2",
  requested_at: "2026-09-11T03:04:00Z",
  decided_by: null, decided_at: null, decision_note: null,
  thread: [], can_decide: true, is_open: true,
  subject: { qty: 400, proposed_price: 298, unit_cost: 306 },
};

const DECISION: DecisionDetail = {
  decision_id: "d1", decision_type: "MARGIN_DETERIORATION",
  subject_entity_type: "CUSTOMER", subject_entity_id: "c1",
  subject_label: "Bharat Forge Ltd", assigned_user_id: null, assigned_role: "OWNER",
  detected_at: "2026-09-09T04:00:00Z",
  priority: { band: "HIGH", score: 91, deterministic_base: 80, ai_adjustment: 11 },
  status: "OPEN",
  facts: [{ label: "landed_cost", value: 447, restricted: false, source: "zoho" }],
  evidence: [], signal: null,
  interpretation: { status: "OK", title: "Quoted below cost three times running",
                    recommendation: "Reprice to ₹545", explanation: "The rate card moved on 12 August.",
                    caveat: null, should_surface: true, model: "m" },
  confidence: {}, human_action: null, outcome: null, origin: "STATE",
  impact: { financial: "184200", basis: "twelve months at the current run rate" },
  rationale: null,
  actions: [{ key: "accept", label: "Reprice to ₹545" }],
  ranking: {}, state_evidence: {}, state: { keys: [], as_of: null },
};

/** A second decision of a given type, so the type filter has something to
 *  narrow. Everything but the type and the title is the one above. */
function costDecision(id: string, title: string): DecisionDetail {
  return {
    ...DECISION,
    decision_id: id,
    decision_type: "COST_PASS_THROUGH",
    subject_label: title,
    interpretation: { ...DECISION.interpretation!, title },
  };
}

function show(over: Partial<Parameters<typeof TodayScreen>[0]> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // Held as their own consts rather than read back off the props object: two of
  // these tests are about *what was passed* — whether a message came with an
  // undo — and spreading them through a props object widens the mock away.
  const flash = vi.fn<(message: string, undo?: () => void) => void>();
  const onDecisionAction = vi.fn<(id: string, actionKey: string) => void>();
  render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={createTheme()}>
        <MemoryRouter>
          <TodayScreen
            session={SESSION}
            decisions={[DECISION]}
            loading={false}
            error={null}
            onReload={vi.fn()}
            onDecisionAction={onDecisionAction}
            onUndoDecision={vi.fn()}
            flash={flash}
            {...over}
          />
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
  return { flash, onDecisionAction };
}

describe("Today", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(papi, "listApprovals").mockResolvedValue(
      { requests: [APPROVAL], pending_for_me: 1 } as never);
    vi.spyOn(papi, "unrecordedQuotes").mockResolvedValue(
      { quotes: [], count: 0 } as never);
  });

  it("opens on the hardest item with its arithmetic and its action already on screen", async () => {
    show();
    // The approval, not the decision: somebody is waiting on this one.
    expect(await screen.findByRole("heading", { name: /TNMG160404 at ₹298/ })).toBeInTheDocument();
    expect(screen.getByText("Rate asked")).toBeInTheDocument();
    // The verb, not "Act on this".
    expect(screen.getByRole("button", { name: /^Approve/ })).toBeInTheDocument();
  });

  it("records an approval where it was read, and moves to the next item", async () => {
    const decide = vi.spyOn(papi, "decideApproval").mockResolvedValue({} as never);
    show();

    fireEvent.click(await screen.findByRole("button", { name: /^Approve/ }));

    await waitFor(() => expect(decide).toHaveBeenCalledWith("t", "ap1", "APPROVED"));
    // Advanced on its own: the decision is now the open item.
    expect(await screen.findByRole("heading", { name: /Quoted below cost/ })).toBeInTheDocument();
  });

  it("keeps what was answered on screen, so the morning has a shape", async () => {
    vi.spyOn(papi, "decideApproval").mockResolvedValue({} as never);
    show();
    fireEvent.click(await screen.findByRole("button", { name: /^Approve/ }));

    const answered = await screen.findByText("TNMG160404 at ₹298");
    expect(answered).toBeInTheDocument();
    expect(screen.getByText("approve")).toBeInTheDocument();
  });

  it("moves with the keyboard without touching anything", async () => {
    show();
    await screen.findByRole("heading", { name: /TNMG160404 at ₹298/ });

    fireEvent.keyDown(window, { key: "j" });
    expect(await screen.findByRole("heading", { name: /Quoted below cost/ })).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "k" });
    expect(await screen.findByRole("heading", { name: /TNMG160404 at ₹298/ })).toBeInTheDocument();
  });

  it("offers undo only where the server can take it back", async () => {
    vi.spyOn(papi, "decideApproval").mockResolvedValue({} as never);
    const { flash, onDecisionAction } = show();

    // The approval: written to an append-only trail, so no undo is offered.
    fireEvent.click(await screen.findByRole("button", { name: /^Approve/ }));
    await waitFor(() => expect(flash).toHaveBeenCalled());
    expect(flash.mock.calls[0][1]).toBeUndefined();

    // The decision: `reopen` exists, so one is.
    fireEvent.click(await screen.findByRole("button", { name: /Reprice to ₹545/ }));
    await waitFor(() => expect(onDecisionAction).toHaveBeenCalledWith("d1", "accept"));
    expect(typeof flash.mock.calls[1][1]).toBe("function");

    // And the done list says which is which, in words rather than a dead button.
    expect(await screen.findByText("Done for today")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Undo" })).toBeInTheDocument();
    expect(screen.getAllByText("recorded").length).toBe(1);
  });

  it("narrows the morning to one type, and starts at the top of what is left", async () => {
    // The fault this answers: a hundred and eight items in one rail, most of
    // them the same kind of thing. Choosing a type is choosing what to work,
    // and it lands on that type's hardest item rather than on whatever sat at
    // the index the cursor happened to hold.
    show({ decisions: [DECISION, costDecision("d2", "Carbide drill up 50.7%"),
                       costDecision("d3", "Insert grade up 12.1%")] });
    await screen.findByRole("heading", { name: /TNMG160404 at ₹298/ });

    fireEvent.mouseDown(screen.getByRole("combobox", { name: /Type/ }));
    // The count travels with the name: choosing this leaves two.
    fireEvent.click(await screen.findByRole("option", { name: /Cost pass-through.*2/ }));

    expect(await screen.findByRole("heading", { name: /Carbide drill up 50.7%/ }))
      .toBeInTheDocument();
    const queue = within(screen.getByRole("list", { name: "Queue" }));
    expect(queue.getByText("Carbide drill up 50.7%")).toBeInTheDocument();
    expect(queue.queryByText("Bharat Forge Ltd")).not.toBeInTheDocument();
    // The morning is still the morning underneath the filter.
    expect(screen.getByText(/2 of 4 to work through/)).toBeInTheDocument();
  });

  it("does not call the morning done when only the filtered type is finished", async () => {
    // Absence of evidence is not a pass, in an interface too: there are three
    // other things waiting, and "Done for today" over a narrowed queue is the
    // screen saying the morning is over because it is looking at a slice of it.
    show({ decisions: [DECISION, costDecision("d2", "Carbide drill up 50.7%")] });
    await screen.findByRole("heading", { name: /TNMG160404 at ₹298/ });

    fireEvent.mouseDown(screen.getByRole("combobox", { name: /Type/ }));
    fireEvent.click(await screen.findByRole("option", { name: /Cost pass-through.*1/ }));
    fireEvent.click(await screen.findByRole("button", { name: /Reprice to ₹545/ }));

    expect(await screen.findByText(/Nothing of this type is left this morning/))
      .toBeInTheDocument();
    expect(screen.queryByText("Done for today")).not.toBeInTheDocument();
    // And the filter still says what it is filtering. MUI renders a select
    // holding a value it has no option for as blank, so a type dropped from
    // the menu the moment it emptied left the control looking unset over a
    // queue that was still narrowed.
    expect(screen.getByRole("combobox", { name: /Type/ }))
      .toHaveTextContent("Cost pass-through");
    // And the way back to the rest of it.
    fireEvent.click(screen.getByRole("button", { name: "Show every type" }));
    expect(await screen.findByRole("heading", { name: /TNMG160404 at ₹298/ }))
      .toBeInTheDocument();
  });

  it("keeps the queue inside its own scroll rather than running down the page", async () => {
    // The rail is capped at the panel's height in CSS — see the comment on it —
    // and this pins the half of that which is not layout: the list is its own
    // scroll container, and it is reachable by keyboard, because a region you
    // can only scroll with a pointer is one half the people on this desk
    // cannot read the bottom of.
    show();
    const queue = await screen.findByRole("list", { name: "Queue" });
    expect(queue).toHaveStyle({ overflowY: "auto" });
    expect(queue).toHaveAttribute("tabindex", "0");
  });

  it("offers no type filter when the morning is all one thing", async () => {
    // A select with one option filters nothing and costs a control in a rail
    // that is short of room — the same rule the company filter follows.
    vi.spyOn(papi, "listApprovals").mockResolvedValue(
      { requests: [], pending_for_me: 0 } as never);
    show({ decisions: [DECISION] });
    await screen.findByRole("heading", { name: /Quoted below cost/ });
    expect(screen.queryByRole("combobox", { name: /Type/ })).not.toBeInTheDocument();
  });

  it("says what it checked when nothing needs anybody", async () => {
    // Not "all clear". Nothing cleared the thresholds, which is a statement
    // about the evidence rather than a verdict on the business.
    vi.spyOn(papi, "listApprovals").mockResolvedValue(
      { requests: [], pending_for_me: 0 } as never);
    show({ decisions: [] });
    expect(await screen.findByText(/Nothing needs you this morning/)).toBeInTheDocument();
    expect(screen.getByText(/No approval is waiting/)).toBeInTheDocument();
  });
});
