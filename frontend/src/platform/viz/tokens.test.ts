// The visualization palette's contract with the charts that draw from it.
//
// The colours themselves are not asserted here — `validate_palette.js` owns
// those, against lightness bands, chroma floors and CVD separation, and a test
// that pinned the hex values would just be a second copy of the palette that
// has to be edited twice.
//
// What is asserted is the structure the charts depend on and the reasoning the
// file's own header states. Every one of these fails silently: a bucket missing
// from `BUCKET_SIGN` draws a bar pointing the wrong way, a palette var the CSS
// references but the object does not emit resolves to nothing and the mark
// renders invisible, and an opacity table that is not ordinal makes a
// three-step confidence scale read as three unrelated categories.
import { describe, expect, it } from "vitest";

import {
  BAND_COLOR,
  BUCKET_LABEL,
  BUCKET_MEANING,
  BUCKET_SHADE,
  BUCKET_SIGN,
  CONFIDENCE_LABEL,
  CONFIDENCE_OPACITY,
  palette,
  paletteVars,
} from "./tokens";

const BUCKETS = ["LOST", "SHRUNK", "STABLE", "GROWN", "RECOVERED", "NEW"];

describe("the flow buckets", () => {
  it("gives every bucket a sign, a label, a meaning and a shade", () => {
    // Four tables keyed the same way. A bucket present in one and missing from
    // another renders a bar with no direction, or a legend entry with no word.
    for (const table of [BUCKET_SIGN, BUCKET_LABEL, BUCKET_MEANING, BUCKET_SHADE]) {
      expect(Object.keys(table).sort()).toEqual([...BUCKETS].sort());
    }
  });

  it("points each bucket the way its name means", () => {
    // The sign drives bar direction as well as colour, so it survives
    // greyscale, forced colours and a printed page — which is the point.
    expect(BUCKET_SIGN.LOST).toBe(-1);
    expect(BUCKET_SIGN.SHRUNK).toBe(-1);
    expect(BUCKET_SIGN.STABLE).toBe(0);
    expect(BUCKET_SIGN.GROWN).toBe(1);
    expect(BUCKET_SIGN.RECOVERED).toBe(1);
    expect(BUCKET_SIGN.NEW).toBe(1);
  });

  it("separates the states inside a direction by lightness, not by hue", () => {
    // Three gains that shared a shade would be indistinguishable; three gains
    // with three hues would read as unrelated categories and lose the
    // direction. So: same sign, different shades, all within the band.
    const gains = ["NEW", "RECOVERED", "GROWN"].map((b) => BUCKET_SHADE[b]);
    expect(new Set(gains).size).toBe(gains.length);
    for (const shade of Object.values(BUCKET_SHADE)) {
      expect(shade).toBeGreaterThan(0);
      expect(shade).toBeLessThanOrEqual(1);
    }
  });
});

describe("confidence", () => {
  it("is one hue at three opacities, ordered the way it is ranked", () => {
    // Ordinal, so the scale has to be monotonic — otherwise "partial" can look
    // more certain than "sufficient", which is the one thing this must not say.
    expect(CONFIDENCE_OPACITY.SUFFICIENT)
      .toBeGreaterThan(CONFIDENCE_OPACITY.PARTIAL);
    expect(CONFIDENCE_OPACITY.PARTIAL)
      .toBeGreaterThan(CONFIDENCE_OPACITY.INSUFFICIENT);
  });

  it("always ships a word beside the opacity", () => {
    // Opacity alone is not readable, and it does not survive a printed report.
    expect(Object.keys(CONFIDENCE_LABEL).sort())
      .toEqual(Object.keys(CONFIDENCE_OPACITY).sort());
  });
});

describe("the reserved band colours", () => {
  it("names every band the weather screen can report", () => {
    expect(Object.keys(BAND_COLOR).sort())
      .toEqual(["FAIR", "GOOD", "POOR", "UNKNOWN"]);
  });

  it("draws every band from a palette variable rather than a literal", () => {
    // Status colours are reserved and theme-driven. A literal here would not
    // follow a theme flip, and the band would keep the light-mode hue on a dark
    // surface — where it was never validated.
    for (const [band, value] of Object.entries(BAND_COLOR)) {
      expect(value, `${band} is not a palette variable`).toMatch(/^var\(--viz-/);
    }
  });
});

describe("palette", () => {
  it("gives light and dark a different value for every role", () => {
    // Same keys, no shared value: a role that forgot to darken keeps a
    // light-mode colour on a dark surface, which is exactly what the header
    // says had to be fixed for the amber step.
    const light = palette(false);
    const dark = palette(true);
    expect(Object.keys(light).sort()).toEqual(Object.keys(dark).sort());
    for (const key of Object.keys(light) as (keyof typeof light)[]) {
      expect(dark[key], `${key} is identical in both modes`).not.toBe(light[key]);
    }
  });

  it("emits a custom property for every role the palette defines", () => {
    // The SVG marks reference these by name, so a role present in the object
    // and absent from the vars resolves to nothing and the mark disappears.
    for (const dark of [false, true]) {
      const vars = paletteVars(dark);
      const roles = Object.keys(palette(dark));
      expect(Object.keys(vars).sort()).toEqual(roles.map((r) => `--viz-${r}`).sort());
      for (const [name, value] of Object.entries(vars)) {
        expect(value, `${name} is empty in ${dark ? "dark" : "light"}`).toBeTruthy();
      }
    }
  });

  it("emits the variables every reserved band references", () => {
    // Closes the loop between the two tables: `BAND_COLOR` names variables, and
    // this is the assertion that those variables are ones `paletteVars` sets.
    const emitted = new Set(Object.keys(paletteVars(false)));
    for (const value of Object.values(BAND_COLOR)) {
      const name = value.slice("var(".length, -1);
      expect(emitted.has(name), `${value} is referenced but never emitted`).toBe(true);
    }
  });
});
