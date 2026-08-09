// The grid's palette has one owner, and this is the check that keeps it that way.
//
// `theme.ts` opens by explaining that the look used to have two owners and that
// "two copies of a hex code diverge on the first tweak, and the failure is
// silent". `DataGridImpl.tsx` was the second copy — seven literal hex codes and
// `browserColorScheme: "light"` — and it is the file every table in the product
// renders through, so a palette change would have moved every surface except the
// grids, and a dark mode would have left them lit.
//
// A source-text check rather than a rendering one, deliberately. What can go
// wrong here is somebody pasting a hex code back in while chasing a pixel, and
// no rendered assertion catches that as clearly as reading the file does — the
// same reasoning as `test_layer_boundaries.py` parsing imports rather than
// trusting a screenshot.
/// <reference types="vite/client" />
import { describe, expect, it } from "vitest";

// The source as text, through Vite's own `?raw` rather than `node:fs`. Reading
// the file with fs needs `@types/node`, which this project does not install, so
// the test passed under vitest and failed `tsc -b` — and `tsc -b` is in the gate.
// `?raw` is typed by `vite/client`, referenced above, and resolves relative to
// this file instead of to whatever the working directory happens to be.
import source from "./DataGridImpl.tsx?raw";
import { theme } from "../theme";

/** The block that configures the grid's look, without the prose above it —
 *  the comment legitimately quotes the literals it replaced. */
const themeBlock = source.slice(
  source.indexOf("function useGridTheme"), source.indexOf("ROW_SELECTION"));

describe("the grid takes its palette from the app theme", () => {
  it("states no colour of its own", () => {
    const hex = themeBlock.match(/#[0-9a-fA-F]{3,8}\b/g) ?? [];
    expect(hex).toEqual([]);
  });

  it("does not pin a colour scheme, so the grid follows the theme's mode", () => {
    expect(themeBlock).not.toMatch(/browserColorScheme:\s*["']/);
    expect(themeBlock).toContain("browserColorScheme: mui.palette.mode");
  });

  it("reads the values it needs off the theme rather than restating them", () => {
    for (const token of [
      "mui.palette.primary.main",
      "mui.palette.divider",
      "mui.palette.text.primary",
      "mui.palette.text.secondary",
      "mui.palette.info.light",
    ]) {
      expect(themeBlock).toContain(token);
    }
  });

  it("the tokens it reads all exist on the theme", () => {
    // Guards the other direction: a rename in `theme.ts` that left this reading
    // `undefined` would hand ag-grid its own defaults, silently.
    expect(theme.palette.primary.main).toBeTruthy();
    expect(theme.palette.divider).toBeTruthy();
    expect(theme.palette.common.white).toBeTruthy();
    expect(theme.palette.text.secondary).toBeTruthy();
    expect(theme.palette.info.light).toBeTruthy();
    expect(theme.typography.body2.fontSize).toBeTruthy();
    expect(theme.typography.overline.fontSize).toBeTruthy();
  });
});
