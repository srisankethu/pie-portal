// A form dialog is full screen on a phone, and a centred box at a desk.
//
// The rule is in `kit.FormDialog`'s docstring and the reason is measured: at
// 390px a centred MUI dialog leaves 32px of backdrop down each side and caps
// itself at 90% of the height, so the Paste RFQ box, the customer search and
// the outcome fields each arrived as a letterbox with its own scrollbar inside
// the page's, with the action row sitting where the snackbar lands.
//
// Worth a test rather than left to review because the branch is invisible in
// the source of every call site: the call sites say `<FormDialog>` and nothing
// else, and jsdom answers every media query false, so a regression that dropped
// the query would look identical in the suite, in review and on a laptop, and
// would only ever show up on a phone.
import { render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FormDialog } from "./kit";
import { pretendViewportIs } from "../test/viewport";

afterEach(() => vi.unstubAllGlobals());

function show() {
  render(
    <ThemeProvider theme={createTheme()}>
      <FormDialog open aria-label="Paste RFQ">
        <p>the form</p>
      </FormDialog>
    </ThemeProvider>,
  );
  // MUI puts `role="dialog"` on the paper itself, so this is the sheet.
  return screen.getByRole("dialog");
}

describe("a form dialog", () => {
  it("takes the whole screen on a phone", () => {
    pretendViewportIs(390);
    expect(show().className).toContain("MuiDialog-paperFullScreen");
  });

  it("stays a centred box at a desk", () => {
    pretendViewportIs(1440);
    const paper = show();
    expect(paper.className).not.toContain("MuiDialog-paperFullScreen");
    // `fullWidth` is the default the call sites used to pass by hand, so the
    // box is still as wide as its `maxWidth` allows rather than shrink-wrapped
    // around its longest line.
    expect(paper.className).toContain("MuiDialog-paperFullWidth");
  });

  it("lets a caller that has decided for itself win", () => {
    pretendViewportIs(1440);
    render(
      <ThemeProvider theme={createTheme()}>
        <FormDialog open fullScreen aria-label="Always">
          <p>the form</p>
        </FormDialog>
      </ThemeProvider>,
    );
    expect(screen.getByRole("dialog").className).toContain("MuiDialog-paperFullScreen");
  });
});
