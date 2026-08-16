/** What this role is *offered*. Not what it is allowed.
 *
 * **This is not a security control.** CASL runs in the browser. The authority
 * is `backend/app/authz.py`, which this mirrors rather than consults. Cost and
 * margin are absent from a salesperson's *response*, not hidden in their
 * browser — if a rule here is the only thing between a salesperson and a
 * margin, the bug is on the server.
 *
 * It exists because a nav item that always 403s teaches people the product is
 * broken, and because it replaces the same rule written eight ways across the
 * nav table, `AdminScreens` and half a dozen components.
 *
 * Every rule cites the server function it reflects. When one changes and this
 * does not, the symptom is a 403 or a missing screen — visible, and not a
 * disclosure. This file is allowed to be wrong only towards offering too little.
 */
import { AbilityBuilder, createMongoAbility, type MongoAbility } from "@casl/ability";

import type { PlatformSession, Role } from "./types";

/** The verbs. Deliberately few — a vocabulary that grows per screen is a
 *  vocabulary nobody can hold in their head while reading a rule. */
export type Action = "read" | "manage" | "approve";

/** The nouns. Each is a thing the interface offers, not a database table:
 *  "economics" is cost and margin wherever they appear, not one endpoint. */
export type Subject =
  | "economics"        // cost, margin, purchase rate — anywhere they surface
  | "team"             // other people's accounts and their decisions
  | "users"            // accounts and roles
  | "policy"           // margin policy, approval policy, thresholds — and the
                       // identity screen, whose reads carry the same
                       // manager-or-owner authority. Reused rather than given a
                       // noun of its own: this vocabulary is meant to stay small
                       // enough to hold in your head, and a subject per screen
                       // is how that stops being true.
  | "approvals"        // the approval queue
  | "supply"           // suppliers, purchasing, what we owe
  | "simulation"       // the what-if desk
  | "trust"            // disclosure, the access log, export and erasure
  | "evaluation"       // the 30-day report: what the platform was worth against
                       // what it costs. A noun of its own rather than a reuse,
                       // because it is the one place where the screen and one
                       // panel inside it have *different* server gates — the
                       // value ledger is manager-or-owner, the report against
                       // the baseline is owner-only — and no existing subject
                       // can say that without lying about one of the two.
  | "all";

export type AppAbility = MongoAbility<[Action, Subject]>;

export function defineAbilityFor(role: Role | undefined): AppAbility {
  const { can, build } = new AbilityBuilder<AppAbility>(createMongoAbility);

  // Everyone signed in. Their own accounts, their own queue, and the screens
  // that carry no cost — mix and rhythm are revenue and dates, stock structure
  // is counts, receivables are money in.
  if (role) {
    can("read", "approvals");
  }

  // Manager and owner. Mirrors `require_manager_or_owner`, which is the
  // dependency guarding every endpoint that returns cost, margin or spend.
  if (role === "SALES_MANAGER" || role === "OWNER") {
    can("read", "economics");
    can("read", "team");
    can("read", "supply");
    can("read", "simulation");
    can("read", "policy");
    // Mirrors `approvals` — a manager signs off a thin price.
    can("approve", "approvals");
  }

  // Owner only. Mirrors `require_owner`: accounts, roles and the policy the
  // rest of the platform is judged against. A manager who could widen their own
  // authority would not have any.
  if (role === "OWNER") {
    can("manage", "users");
    can("manage", "policy");
    // Selling below cost is an owner's signature, never a manager's.
    can("approve", "all");
    // Mirrors `require_owner` on every `/trust/*` route. Who has looked at our
    // data and what left for a model are questions about the relationship with
    // the vendor, not about running the desk — and erasure destroys the tenant's
    // data key, which is not a decision to offer a manager by accident.
    can("read", "trust");
    can("manage", "trust");
    // Mirrors `require_owner` on `GET /api/attribution/evaluation`. Whether the
    // platform earned its price is the renewal conversation, and it sets one
    // book's performance before the platform against its performance during —
    // an owner's question, not a desk one. The ledger it is computed from stays
    // manager-or-owner, so this gates a panel rather than the screen.
    can("read", "evaluation");
  }

  return build();
}

/** Convenience for the common question, so call sites read as English. */
export function abilityFor(session: PlatformSession | null): AppAbility {
  return defineAbilityFor(session?.role);
}

/** Where this is deliberately NOT used, and why.
 *
 * `SettingsScreen` gates its editing controls on `can_manage`, which the server
 * puts on the users response. That is a better answer than anything here: it is
 * the authority speaking about this exact request, rather than a mirror of it
 * reconstructed in a browser. Replacing it with `ability.can("manage", ...)`
 * would look tidier and would be a downgrade.
 *
 * The rule of thumb, then: use this when the client must decide what to offer
 * *before* it has asked anything — the nav is built before any request. When
 * the server has already answered, use its answer.
 */
export const WHERE_THE_SERVER_ANSWERS_INSTEAD = [
  "SettingsScreen: can_manage, from GET /admin/users",
  "ApprovalCard: can_decide, per request, from GET /admin/approvals",
] as const;
