// The session the shell's authorization hangs off, parsed rather than claimed.
//
// `JSON.parse(raw) as PlatformSession` is an assertion the compiler cannot
// check, and this is the one place the data genuinely is not ours: localStorage,
// which a browser extension, a shared machine or an older build of this app may
// have written.
//
// Two properties matter, and they pull in opposite directions. It has to be
// strict about `role`, because a session carrying a role this build does not
// know would fall through every `role === "OWNER"` check and quietly render one
// person's view to another. And it has to be forgiving about *shape*, because
// throwing happens during module load and takes the page down with a blank
// screen — which is worse than asking somebody to sign in again.
import { describe, expect, it } from "vitest";

import { parseStoredSession, platformSessionSchema } from "./schemas";

const VALID = {
  token: "tok_1",
  role: "SALES_MANAGER",
  name: "M. Rao",
  user_id: "usr_manager",
  organization_id: "org_sanketh",
};

describe("a session that is one", () => {
  it("accepts the fields every session has", () => {
    expect(parseStoredSession(JSON.stringify(VALID))).toMatchObject(VALID);
  });

  it("accepts each of the three roles the product has", () => {
    for (const role of ["SALESPERSON", "SALES_MANAGER", "OWNER"]) {
      expect(parseStoredSession(JSON.stringify({ ...VALID, role })))
        .toMatchObject({ role });
    }
  });

  it("accepts a session stored before the optional fields existed", () => {
    // An older build wrote sessions without currency, timezone, email or the
    // must-change flag. Those are still sessions; the app falls back to its
    // defaults rather than showing the door to somebody already signed in.
    const parsed = parseStoredSession(JSON.stringify(VALID));
    expect(parsed).not.toBeNull();
    expect(parsed?.currency).toBeUndefined();
    expect(parsed?.must_change_password).toBeUndefined();
  });

  it("keeps the optional fields when they are there", () => {
    const full = { ...VALID, currency: "INR", timezone: "Asia/Kolkata",
                   email: "m.rao@sanketh.in", must_change_password: true };
    expect(parseStoredSession(JSON.stringify(full))).toMatchObject(full);
  });
});

describe("a session that is not one", () => {
  it("refuses a role this build does not know", () => {
    // The assertion this file exists for. `ADMIN` is not a role here, and a
    // session carrying it must fail at the door rather than fall through every
    // equality check as "not OWNER, not SALES_MANAGER" and land on the
    // salesperson's view — or, worse, past a `!== "SALESPERSON"` guard.
    expect(parseStoredSession(JSON.stringify({ ...VALID, role: "ADMIN" }))).toBeNull();
    expect(parseStoredSession(JSON.stringify({ ...VALID, role: "" }))).toBeNull();
    expect(parseStoredSession(JSON.stringify({ ...VALID, role: "owner" }))).toBeNull();
  });

  it("refuses a session missing an identity it is supposed to carry", () => {
    // `token` was in this list and is deliberately no longer: it moved to an
    // httpOnly cookie, so an empty one is the normal stored value rather than a
    // broken session. The identities below are still required, because they are
    // what the shell draws itself from and a blank one would render a signed-in
    // frame belonging to nobody.
    for (const key of ["user_id", "organization_id"]) {
      expect(parseStoredSession(JSON.stringify({ ...VALID, [key]: "" })),
             `an empty ${key} is not a session`).toBeNull();
      const { [key]: _dropped, ...without } = VALID as Record<string, unknown>;
      expect(parseStoredSession(JSON.stringify(without)),
             `a missing ${key} is not a session`).toBeNull();
    }
  });

  it("accepts an empty token, because the cookie is the credential now", () => {
    // The counterpart to the loop above, stated as its own assertion so the
    // change reads as a decision rather than as a rule somebody quietly dropped.
    // Storing a token at all is what this change removed; a stored session is a
    // profile, and `/auth/me` against the cookie is what says it is still live.
    expect(parseStoredSession(JSON.stringify({ ...VALID, token: "" })))
      .toMatchObject({ user_id: VALID.user_id, token: "" });

    // The key must still be present, though — a stored object without it is not
    // one this app wrote, and guessing at its shape is how the old `as` cast
    // booted a signed-in shell over nothing.
    const { token: _dropped, ...without } = VALID as Record<string, unknown>;
    expect(parseStoredSession(JSON.stringify(without))).toBeNull();
  });

  it("refuses a field of the wrong type rather than coercing it", () => {
    expect(parseStoredSession(JSON.stringify({ ...VALID, token: 1 }))).toBeNull();
    expect(parseStoredSession(JSON.stringify({ ...VALID, must_change_password: "true" })))
      .toBeNull();
  });
});

describe("never throwing", () => {
  it("returns null for anything that is not JSON at all", () => {
    // This runs during module load. An exception here is a blank page, which
    // is the one outcome worse than the sign-in card.
    for (const raw of ["", "not json", "{", "undefined", "[1,2,3]", "null", '"a string"']) {
      expect(() => parseStoredSession(raw), `threw on ${JSON.stringify(raw)}`).not.toThrow();
      expect(parseStoredSession(raw), `accepted ${JSON.stringify(raw)}`).toBeNull();
    }
  });
});

describe("the schema itself", () => {
  it("is exported so login can check what it stores, not only what it reads", () => {
    // Both directions go through one definition — a session written by login
    // and a session read back at boot are the same shape by construction.
    expect(platformSessionSchema.safeParse(VALID).success).toBe(true);
    expect(platformSessionSchema.safeParse({ ...VALID, role: "ADMIN" }).success).toBe(false);
  });
});
