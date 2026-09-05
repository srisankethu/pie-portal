// The review's edit rule, tested where it can actually be reached.
//
// One rule, and it exists because of a trap: a binding needs a slot AND a
// type, and nothing on the screen says so — so choosing an attribute on a row
// nobody had named saved *nothing*. The row looked answered and the decoder
// did not read it.
//
// Tested here rather than through the dialog because reaching it that way
// means driving ag-grid's cell editor through the DOM, which tests the grid.
// The reducer is pure and exported for exactly this reason.
import { describe, expect, it } from "vitest";

import { nextEdits } from "./CatalogSources";

const NUMERIC = ["integer", "number", "text"];

describe("the binding review's edit rule", () => {
  it("defaults the type when a slot is chosen on a row nobody had named", () => {
    const next = nextEdits({}, "s1/num1", { slot: "cutting_dia_mm" }, NUMERIC);
    expect(next["s1/num1"]).toEqual({ slot: "cutting_dia_mm", type: "integer" });
  });

  it("defaults to the narrowest type the group's own values support", () => {
    // A group holding 5.1 cannot be an integer, and the server refuses one —
    // so the default has to come from the evidence rather than from a constant.
    const next = nextEdits({}, "s1/num1", { slot: "cutting_dia_mm" },
                           ["number", "text"]);
    expect(next["s1/num1"].type).toBe("number");
  });

  it("keeps a type somebody chose explicitly", () => {
    const prev = { "s1/num1": { slot: "cutting_dia_mm", type: "number" } };
    const next = nextEdits(prev, "s1/num1", { slot: "shank_dia_mm" }, NUMERIC);
    expect(next["s1/num1"]).toEqual({ slot: "shank_dia_mm", type: "number" });
  });

  it("changes only the type when that is what changed", () => {
    const prev = { "s1/num1": { slot: "cutting_dia_mm", type: "integer" } };
    const next = nextEdits(prev, "s1/num1", { type: "text" }, NUMERIC);
    expect(next["s1/num1"]).toEqual({ slot: "cutting_dia_mm", type: "text" });
  });

  it("clears the type when the slot is cleared", () => {
    // A type without a slot is not a binding, and leaving one behind would
    // make the next slot chosen inherit a type from an attribute it has
    // nothing to do with.
    const prev = { "s1/num1": { slot: "cutting_dia_mm", type: "integer" } };
    const next = nextEdits(prev, "s1/num1", { slot: "" }, NUMERIC);
    expect(next["s1/num1"]).toEqual({ slot: "", type: "" });
  });

  it("leaves every other row alone", () => {
    const prev = { "s1/num1": { slot: "loc_mm", type: "number" } };
    const next = nextEdits(prev, "s1/num2", { slot: "oal_mm" }, NUMERIC);
    expect(next["s1/num1"]).toEqual({ slot: "loc_mm", type: "number" });
    expect(next["s1/num2"]).toEqual({ slot: "oal_mm", type: "integer" });
  });

  it("falls back to text where a group survives no numeric type", () => {
    expect(nextEdits({}, "s1/opt1", { slot: "through_coolant" }, [])
      ["s1/opt1"].type).toBe("text");
  });
});
