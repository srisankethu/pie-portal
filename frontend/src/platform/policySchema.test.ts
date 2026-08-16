// The margin policy's rules, as the settings form applies them.
//
// This is the *client* half of a rule the server also enforces, and the two
// have to say the same thing for a different reason than usual: the server's
// refusal is what protects the data, but a form that lets an impossible ladder
// be typed and only objects after Save is the thing this module was written to
// end. If the mirror drifts, nobody is unsafe — they are just told the wrong
// thing at the wrong moment, repeatedly.
//
// The ladder is the rule worth pinning: approval floor ≤ review floor ≤ target
// margin, "at or below" in both places. The equality cases are here because
// they are the ones a test written with round numbers never reaches, and
// because a rung quietly tightened to `>=` would reject a policy that is
// perfectly legal — the failure that looks like the form being broken.
//
// Note the unit. These are percentages (0–100) because the boxes say "%",
// where the server's are ratios. Anything comparing the two directly is
// comparing 24 with 0.24.
import { describe, expect, it } from "vitest";

import { LADDER, policyProblems } from "./policySchema";
import type { PolicyField, PolicyKind } from "./types";

function field(name: string, kind: PolicyKind = "ratio", label?: string): PolicyField {
  return {
    field: name,
    label: label ?? name.replace(/_/g, " "),
    help: "",
    value: 0,
    default: 0,
    overridden: false,
    kind,
  };
}

/** The three ladder fields the server offers today, in ladder order. */
const LADDER_FIELDS = LADDER.map((f) => field(f));

/** A draft that satisfies every rule — the baseline each case edits one box of. */
const VALID = { min_margin: "12", margin_floor: "15", target_margin_default: "24" };

describe("the floor ladder", () => {
  it("accepts a draft where each floor sits under the next", () => {
    expect(policyProblems(LADDER_FIELDS, VALID)).toEqual({});
  });

  it("accepts floors that are exactly equal", () => {
    // "At or below", not "below". All three equal is a legal policy: every line
    // is simultaneously at target, at the review floor and at the approval
    // floor, which is unusual but not contradictory. Rejecting it would be the
    // form refusing something the server accepts.
    expect(policyProblems(LADDER_FIELDS, {
      min_margin: "20", margin_floor: "20", target_margin_default: "20",
    })).toEqual({});
  });

  it("refuses an approval floor above the review floor, on that field", () => {
    // The message belongs under the box that is wrong. A banner makes the
    // reader hunt for which of three numbers to change.
    const problems = policyProblems(LADDER_FIELDS, { ...VALID, min_margin: "18" });
    expect(Object.keys(problems)).toEqual(["min_margin"]);
    expect(problems.min_margin).toMatch(/at or below the review floor/);
  });

  it("refuses a review floor above the target margin, on that field", () => {
    const problems = policyProblems(LADDER_FIELDS, {
      ...VALID, margin_floor: "30",
    });
    expect(problems.margin_floor).toMatch(/at or below the target margin/);
    expect(problems.min_margin).toBeUndefined();
  });

  it("reports one point past each rung, not a rounding away from it", () => {
    // 15 over 15 is fine; 15.01 over 15 is not. Both rungs, so neither can be
    // loosened on its own without a test noticing.
    expect(policyProblems(LADDER_FIELDS, { ...VALID, min_margin: "15" })).toEqual({});
    expect(policyProblems(LADDER_FIELDS, { ...VALID, min_margin: "15.01" }))
      .toHaveProperty("min_margin");
    expect(policyProblems(LADDER_FIELDS, { ...VALID, margin_floor: "24" })).toEqual({});
    expect(policyProblems(LADDER_FIELDS, { ...VALID, margin_floor: "24.01" }))
      .toHaveProperty("margin_floor");
  });

  it("says nothing about the ladder while a box is still being typed", () => {
    // A cleared box is somebody mid-edit. Complaining that a blank field
    // inverts the ladder is noise on top of the blank-field message that is
    // already there and already correct.
    const problems = policyProblems(LADDER_FIELDS, { ...VALID, margin_floor: "" });
    expect(problems.margin_floor).toMatch(/cannot be blank/);
    expect(problems.min_margin).toBeUndefined();
    expect(problems.target_margin_default).toBeUndefined();
  });
});

describe("what a box will accept", () => {
  it("names the field in every message, because the message sits under it", () => {
    const problems = policyProblems([field("min_margin", "ratio", "Approval floor")],
                                    { min_margin: "" });
    expect(problems.min_margin).toContain("Approval floor");
  });

  it("treats a ratio as a percentage between 0 and 100 inclusive", () => {
    const only = [field("min_margin")];
    expect(policyProblems(only, { min_margin: "0" })).toEqual({});
    expect(policyProblems(only, { min_margin: "100" })).toEqual({});
    expect(policyProblems(only, { min_margin: "-1" })).toHaveProperty("min_margin");
    expect(policyProblems(only, { min_margin: "101" })).toHaveProperty("min_margin");
  });

  it("refuses something that is not a number at all", () => {
    const problems = policyProblems([field("min_margin")], { min_margin: "twelve" });
    expect(problems.min_margin).toMatch(/must be a number/);
  });

  it("wants days whole and at least one", () => {
    const only = [field("recent_days", "days")];
    expect(policyProblems(only, { recent_days: "1" })).toEqual({});
    expect(policyProblems(only, { recent_days: "0" })).toHaveProperty("recent_days");
    expect(policyProblems(only, { recent_days: "1.5" })).toHaveProperty("recent_days");
  });

  it("allows money to be zero but not negative", () => {
    // Zero is a real setting — it switches the threshold off. Negative is not a
    // setting, it is a typo.
    const only = [field("min_material_gap", "money")];
    expect(policyProblems(only, { min_material_gap: "0" })).toEqual({});
    expect(policyProblems(only, { min_material_gap: "-5" }))
      .toHaveProperty("min_material_gap");
  });

  it("takes band edges as numbers separated by commas or spaces", () => {
    const only = [field("quantity_band_edges", "band_edges")];
    expect(policyProblems(only, { quantity_band_edges: "1,10,50,200" })).toEqual({});
    expect(policyProblems(only, { quantity_band_edges: "1 10 50" })).toEqual({});
    expect(policyProblems(only, { quantity_band_edges: "" }))
      .toHaveProperty("quantity_band_edges");
    expect(policyProblems(only, { quantity_band_edges: "1,ten" }))
      .toHaveProperty("quantity_band_edges");
  });

  it("takes a flag only as the two strings its switch emits", () => {
    const only = [field("require_approval_for_quotes", "flag")];
    expect(policyProblems(only, { require_approval_for_quotes: "true" })).toEqual({});
    expect(policyProblems(only, { require_approval_for_quotes: "false" })).toEqual({});
    expect(policyProblems(only, { require_approval_for_quotes: "yes" }))
      .toHaveProperty("require_approval_for_quotes");
  });
});

describe("the shape of the answer", () => {
  it("builds itself from the fields the server offered, not a hardcoded list", () => {
    // The point of the runtime schema: a threshold the backend adds tomorrow is
    // validated without a change here. A field absent from `fields` is not the
    // form's business, so a value under that key is not judged.
    const problems = policyProblems([field("a_new_threshold")],
                                    { a_new_threshold: "999", not_offered: "nonsense" });
    expect(Object.keys(problems)).toEqual(["a_new_threshold"]);
  });

  it("leaves the family margins to their own editor", () => {
    // `family_margins` is a map with its own control and its own rules; running
    // the ratio rule over it would reject every valid value.
    expect(policyProblems([field("target_margin_by_family", "family_margins")],
                          { target_margin_by_family: "{}" })).toEqual({});
  });

  it("reports one problem per field, the first", () => {
    // Two messages under one box is one more than anybody reads, and the second
    // is usually a consequence of the first.
    const problems = policyProblems(LADDER_FIELDS,
                                    { min_margin: "", margin_floor: "", target_margin_default: "" });
    for (const message of Object.values(problems)) {
      expect(typeof message).toBe("string");
    }
    expect(Object.keys(problems).sort()).toEqual([...LADDER].sort());
  });
});
