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
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { papi } from "../api";
import type { ApprovalRequest, DecisionDetail, PlatformSession } from "../types";
import TodayScreen from "./TodayScreen";

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
