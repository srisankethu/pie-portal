// What a grid does when there is not enough width for its columns.
//
// The Money screen is what made this a rule. `sizeColumnsToFit()` divides the
// space between the columns and stops at each column's own minimum, which
// ag-grid defaults to 50px, so at 390px the credit grid rendered six columns of
// about 34px: "Account" beside "O…", "O…", "C…", over cells reading "₹…". Every
// number was on the page and none of them was readable, and because the grid
// had not overflowed anything, no check that looks for a page scrolling
// sideways could see it.
//
// The floor is what turns that back into a grid somebody can scroll. It is
// asserted here rather than through a rendered grid because ag-grid does no
// layout in jsdom — there are no column widths to measure — so the honest thing
// to pin is the decision itself.
import { describe, expect, it } from "vitest";

import { NARROW_BREAKPOINT, fitParamsAt } from "./DataGrid";

describe("fitting columns to the grid", () => {
  it("gives a phone a floor a money figure fits in", () => {
    const params = fitParamsAt(390);
    // "₹2,94,000" plus cell padding and the sort arrow. The exact number is the
    // module's to choose; what this pins is that there is one and it is not the
    // 50px ag-grid would otherwise shrink to.
    expect(params?.defaultMinWidth).toBeGreaterThanOrEqual(100);
  });

  it("leaves a desk alone", () => {
    // ag-grid raises a column's own `minWidth` to this floor rather than only
    // supplying one where none was declared, so a floor at every width would
    // re-lay-out every wide grid whose columns ask for less — the pricing
    // model's scorecard declares 100 and 110.
    expect(fitParamsAt(1440)).toBeUndefined();
    expect(fitParamsAt(NARROW_BREAKPOINT)).toBeUndefined();
  });

  it("switches at the width the cards switch at", () => {
    // One definition of "not enough room", not two. Below this the wrapper
    // draws cards where a screen offers them, and floors the columns where it
    // does not.
    expect(fitParamsAt(NARROW_BREAKPOINT - 1)).toBeDefined();
  });
});
