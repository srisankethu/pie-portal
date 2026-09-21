/** The order of a morning, and the two things it must not do.
 *
 * The order is a policy rather than a score — see the header of `queue.ts` —
 * and a policy is exactly the kind of thing that decays silently when somebody
 * adds a source. So the order is pinned here, along with the two properties
 * that would be defects rather than preferences: the pile of unanswered quotes
 * is capped at what a morning holds, and an approval nobody can decide is shown
 * as a status with no buttons on it rather than hidden or made actionable.
 */
import { describe, expect, it } from "vitest";

import type { ApprovalRequest, DecisionDetail, UnrecordedQuote } from "../types";
import { buildQueue, OUTCOMES_PER_MORNING, queueTypes } from "./queue";

function approval(over: Partial<ApprovalRequest> = {}): ApprovalRequest {
  return {
    approval_request_id: "ap1", kind: "QUOTE_LINE_PRICE", status: "PENDING",
    required_authority: "OWNER", subject_id: "q1", subject_line_id: "l1",
    title: "TNMG at ₹298", summary: "Below landed cost on 400 pieces.",
    reason: "Matching a competitor", reason_code: null,
    requested_by: "Ravi K", requested_by_user_id: "u2",
    requested_at: "2026-09-11T03:04:00Z",
    decided_by: null, decided_at: null, decision_note: null,
    thread: [], can_decide: true, is_open: true,
    subject: { qty: 400, proposed_price: 298, unit_cost: 306, proposed_margin: -0.027 },
    ...over,
  };
}

function decision(over: Partial<DecisionDetail> = {}): DecisionDetail {
  return {
    decision_id: "d1", decision_type: "MARGIN_DETERIORATION",
    subject_entity_type: "CUSTOMER", subject_entity_id: "c1",
    subject_label: "Bharat Forge Ltd", assigned_user_id: null, assigned_role: "OWNER",
    detected_at: "2026-09-09T04:00:00Z",
    priority: { band: "HIGH", score: 91, deterministic_base: 80, ai_adjustment: 11 },
    status: "OPEN", facts: [], evidence: [], signal: null,
    interpretation: { status: "OK", title: "Priced below cost three times running",
                      recommendation: "Reprice to ₹545", explanation: "The rate card moved.",
                      caveat: null, should_surface: true, model: "m" },
    confidence: {}, human_action: null, outcome: null, origin: "STATE",
    impact: { financial: "184200", basis: "twelve months at the current run rate" },
    rationale: null,
    actions: [{ key: "accept", label: "Reprice to ₹545" },
              { key: "dismiss", label: "Dismiss" }],
    ranking: {}, state_evidence: {}, state: { keys: [], as_of: null },
    ...over,
  };
}

function quote(ref: string): UnrecordedQuote {
  return {
    quote_document_ref: ref, connection_id: "cx_sls", number: ref,
    customer_id: "c9",
    customer_label: "Pitti Engineering", source_status: "sent",
    raised_on: "2026-04-14", expires_on: "2026-05-14", group: "PAST_EXPIRY",
    days_past_expiry: 120, value: 418200, opened_at: null,
  };
}

describe("this morning, in order", () => {
  it("puts a price somebody is waiting on before anything the platform found", () => {
    // Not because it is worth more. Because a person is waiting and a quote
    // cannot be sent until they are answered, and no arithmetic here can
    // compare that to a modelled annual bleed.
    const q = buildQueue({
      approvals: [approval()], decisions: [decision()], unanswered: [quote("QT-1")],
    });
    expect(q.map((i) => i.kind)).toEqual(["approval", "decision", "outcome"]);
  });

  it("keeps the server's ranking of decisions rather than re-sorting them", () => {
    const first = decision({ decision_id: "d-first" });
    const second = decision({ decision_id: "d-second",
                              priority: { band: "LOW", score: 4,
                                          deterministic_base: 4, ai_adjustment: 0 } });
    const q = buildQueue({ approvals: [], decisions: [first, second], unanswered: [] });
    expect(q.map((i) => i.id)).toEqual(["decision:d-first", "decision:d-second"]);
  });

  it("asks the oldest waiting approval first", () => {
    const old = approval({ approval_request_id: "older", requested_at: "2026-09-10T09:00:00Z" });
    const recent = approval({ approval_request_id: "newer", requested_at: "2026-09-11T09:00:00Z" });
    const q = buildQueue({ approvals: [recent, old], decisions: [], unanswered: [] });
    expect(q.map((i) => i.id)).toEqual(["approval:older", "approval:newer"]);
  });

  it("asks about three quotes, not about the pile", () => {
    const pile = Array.from({ length: 40 }, (_, i) => quote(`QT-${i}`));
    const q = buildQueue({ approvals: [], decisions: [], unanswered: pile });
    expect(q).toHaveLength(OUTCOMES_PER_MORNING);
  });

  it("shows an approval you cannot decide, last, with no buttons and the reason", () => {
    // A salesperson's own request is the thing they most want the status of.
    // Hiding it sends them to ask in person; offering buttons that 403 is worse.
    const theirs = approval({
      approval_request_id: "mine-to-wait-on", can_decide: false,
      cannot_decide_reason: "Waiting on Sanketh since 08:34.",
    });
    const q = buildQueue({ approvals: [theirs], decisions: [decision()], unanswered: [] });
    expect(q.map((i) => i.kind)).toEqual(["decision", "approval"]);
    expect(q[1].actions).toEqual([]);
    expect(q[1].waiting).toBe("Waiting on Sanketh since 08:34.");
  });

  it("leaves a settled approval out of the morning entirely", () => {
    const q = buildQueue({
      approvals: [approval({ is_open: false, status: "APPROVED" })],
      decisions: [], unanswered: [],
    });
    expect(q).toHaveLength(0);
  });
});

describe("what an item says about money", () => {
  it("carries the impact figure with the sentence that says what it is", () => {
    // A figure without its basis invites the reader to assume the wrong claim —
    // capital locked and annual bleed are different things.
    const [item] = buildQueue({ approvals: [], decisions: [decision()], unanswered: [] });
    expect(item.cost).toContain("1,84,200");
    expect(item.costNote).toBe("twelve months at the current run rate");
  });

  it("says nothing rather than zero where the server withheld the economics", () => {
    // A salesperson's approval arrives with no `subject` at all. The item must
    // render as "not shown for your role", never as ₹0.
    const [item] = buildQueue({
      approvals: [approval({ subject: undefined })], decisions: [], unanswered: [],
    });
    expect(item.cost).toBeNull();
    expect(item.rows).toEqual([]);
  });

  it("keeps a quote with no total on the list and says the total is missing", () => {
    const [item] = buildQueue({
      approvals: [], decisions: [],
      unanswered: [{ ...quote("QT-9"), value: null }],
    });
    expect(item.cost).toBeNull();
    expect(item.costNote).toMatch(/no total/);
  });
});

describe("the types a morning holds", () => {
  it("groups on what repeats, not on the label the rail shows", () => {
    // The reason `typeKey` exists at all. Two unanswered quotes carry two
    // different kind labels — the age of each quote is in it — so grouping on
    // what the rail prints would put one quote in each of a hundred groups and
    // make the filter useless exactly where the pile is biggest.
    const types = queueTypes(buildQueue({
      approvals: [], decisions: [],
      unanswered: [{ ...quote("QT-1"), days_past_expiry: 4 },
                   { ...quote("QT-2"), days_past_expiry: 91 }],
    }));
    expect(types).toEqual([
      { key: "outcome", label: "Quotes with no outcome", count: 2 },
    ]);
  });

  it("separates decisions by their own type and leaves approvals as one", () => {
    const types = queueTypes(buildQueue({
      approvals: [approval({ approval_request_id: "a1" }),
                  approval({ approval_request_id: "a2",
                             required_authority: "MANAGER" })],
      decisions: [decision({ decision_id: "d1" }),
                  decision({ decision_id: "d2", decision_type: "COST_PASS_THROUGH" })],
      unanswered: [],
    }));
    expect(types.map((t) => [t.key, t.count])).toEqual([
      ["approval", 2],
      ["decision:COST_PASS_THROUGH", 1],
      ["decision:MARGIN_DETERIORATION", 1],
    ]);
    // Named for the reader, not for the wire — the same words the decisions
    // list puts on its chips.
    expect(types[1].label).toBe("Cost pass-through");
  });

  it("puts the type that swamped the morning at the top", () => {
    // A filter is reached for when one type has taken the morning over, so
    // that one is the one under the cursor when the menu opens.
    const types = queueTypes(buildQueue({
      approvals: [approval()],
      decisions: Array.from({ length: 5 }, (_, i) =>
        decision({ decision_id: `d${i}`, decision_type: "COST_PASS_THROUGH" })),
      unanswered: [],
    }));
    expect(types[0]).toEqual({ key: "decision:COST_PASS_THROUGH",
                               label: "Cost pass-through", count: 5 });
  });

  it("counts nothing when nothing is left", () => {
    expect(queueTypes([])).toEqual([]);
  });
});
