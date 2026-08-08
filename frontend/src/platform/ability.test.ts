// The role gating table, and the mirror it is supposed to be.
//
// Read `ability.ts`'s own header first: this is a statement about the interface,
// never a security control. The server omits cost and margin from a
// salesperson's response; if a rule here were the only thing between a
// salesperson and a margin, the bug would be on the server.
//
// So these tests do not claim to prove the invariant. What they pin is the
// thing this file *can* get wrong on its own: the mirror drifting. Every
// assertion below names the server rule it reflects, and the direction of each
// failure is the deliberate asymmetry — this table is allowed to offer too
// little, never too much.
import { describe, expect, it } from "vitest";

import { defineAbilityFor } from "./ability";
import type { Role } from "./types";

const ROLES: Role[] = ["SALESPERSON", "SALES_MANAGER", "OWNER"];

describe("economics — mirrors require_manager_or_owner", () => {
  it("is not offered to a salesperson", () => {
    expect(defineAbilityFor("SALESPERSON").can("read", "economics")).toBe(false);
  });

  it("is offered to a manager and an owner", () => {
    expect(defineAbilityFor("SALES_MANAGER").can("read", "economics")).toBe(true);
    expect(defineAbilityFor("OWNER").can("read", "economics")).toBe(true);
  });

  it("is not offered when there is no session at all", () => {
    // A nav built before sign-in must not offer a cost screen and then 403.
    expect(defineAbilityFor(undefined).can("read", "economics")).toBe(false);
  });
});

describe("the salesperson's table", () => {
  const sales = defineAbilityFor("SALESPERSON");

  it("offers only the queue they are in", () => {
    expect(sales.can("read", "approvals")).toBe(true);
  });

  it("offers nothing that carries cost, spend or another person's decisions", () => {
    // "economics" is cost and margin wherever they surface; "supply" is what we
    // owe; "team" is other people's decisions. None is a salesperson's.
    for (const subject of ["economics", "supply", "team", "simulation", "policy"] as const) {
      expect(sales.can("read", subject)).toBe(false);
    }
  });

  it("cannot approve or manage anything", () => {
    expect(sales.can("approve", "approvals")).toBe(false);
    expect(sales.can("manage", "users")).toBe(false);
    expect(sales.can("manage", "policy")).toBe(false);
  });
});

describe("the manager's table — mirrors require_manager_or_owner and approvals", () => {
  const manager = defineAbilityFor("SALES_MANAGER");

  it("signs off a thin price", () => {
    expect(manager.can("approve", "approvals")).toBe(true);
  });

  it("cannot widen its own authority", () => {
    // A manager who could edit the policy they are judged against would not
    // have one. Mirrors `require_owner`.
    expect(manager.can("manage", "policy")).toBe(false);
    expect(manager.can("manage", "users")).toBe(false);
  });

  it("cannot give the owner's signature", () => {
    // Selling below cost is an owner's signature. `approve all` is the rule
    // that carries it, and a manager must not hold it.
    expect(manager.can("approve", "all")).toBe(false);
  });
});

describe("the owner's table — mirrors require_owner", () => {
  const owner = defineAbilityFor("OWNER");

  it("manages accounts and the policy", () => {
    expect(owner.can("manage", "users")).toBe(true);
    expect(owner.can("manage", "policy")).toBe(true);
  });

  it("holds the signature that a manager does not", () => {
    expect(owner.can("approve", "all")).toBe(true);
  });
});

describe("the table as a whole", () => {
  it("never offers a salesperson more than a manager, or a manager more than an owner", () => {
    // The invariant that survives a rule being added carelessly: authority is
    // strictly nested, so a new `can(...)` in the wrong block fails here rather
    // than shipping as a screen someone should not have been offered.
    const subjects = [
      "economics", "team", "users", "policy", "approvals", "supply", "simulation",
    ] as const;

    const sales = defineAbilityFor("SALESPERSON");
    const manager = defineAbilityFor("SALES_MANAGER");
    const owner = defineAbilityFor("OWNER");

    for (const subject of subjects) {
      for (const action of ["read", "manage", "approve"] as const) {
        if (sales.can(action, subject)) {
          expect(manager.can(action, subject)).toBe(true);
        }
        if (manager.can(action, subject)) {
          expect(owner.can(action, subject)).toBe(true);
        }
      }
    }
  });

  it("gives every known role a usable session", () => {
    // A role the table forgot would silently be offered nothing at all, which
    // reads to the person signing in as a broken product.
    for (const role of ROLES) {
      expect(defineAbilityFor(role).can("read", "approvals")).toBe(true);
    }
  });
});
