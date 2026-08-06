/** What this role is *offered*. Not what it is allowed.
 *
 * ## Read this before using it for anything
 *
 * CASL runs in the browser, so every rule below is a statement about the user
 * interface and none of them is a security control. The authority is
 * `backend/app/authz.py`, and it is not consulted here — it is *mirrored*
 * here, which is a different and much weaker thing.
 *
 * The platform's hardest invariant is that cost and margin are **absent from a
 * salesperson's response**, not hidden in their browser: the server omits the
 * fields, so there is nothing to read out of a network tab. Nothing in this
 * file changes that, and nothing in this file should ever be the reason a
 * number is or is not sent. If a rule here is the only thing standing between
 * a salesperson and a margin, the bug is on the server.
 *
 * ## Then why have it
 *
 * Because a nav item that always 403s teaches people the product is broken.
 * The role gating it replaces was a scatter of `session.role !== "SALESPERSON"`
 * ternaries inline in the nav table, in `AdminScreens`, and in half a dozen
 * components — the same rule written eight ways, where the eighth is the one
 * that disagrees. This is one table, next to a comment saying which server
 * rule each line mirrors, so a reader can check the mirror.
 *
 * ## Keeping the mirror true
 *
 * Every rule cites the server function it reflects. When one of those changes
 * and this does not, the symptom is a button that 403s or a screen nobody can
 * find — annoying, visible, and not a disclosure. That asymmetry is deliberate:
 * this file is allowed to be wrong in the direction of offering too little.
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
  | "policy"           // margin policy, approval policy, thresholds
  | "approvals"        // the approval queue
  | "supply"           // suppliers, purchasing, what we owe
  | "simulation"       // the what-if desk
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
