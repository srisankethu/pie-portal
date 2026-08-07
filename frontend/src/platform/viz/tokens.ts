// The visualization palette — computed and validated, not chosen by eye.
//
// Every value below passed `validate_palette.js` against the surface it is drawn
// on, in both modes: lightness band, chroma floor, CVD separation, normal-vision
// separation and contrast. Re-run it before changing any of them; the amber step
// in particular had to be darkened for dark mode to stay inside the band.
//
// **Loss/gain is blue↔red, not green↔red.** Red-green is the conventional
// financial pairing and the single worst choice for colour-vision deficiency —
// it is the one pair a deuteranope cannot separate, and a waterfall is exactly
// the chart where the sign is the whole message. Blue↔red measures ΔE 16.6
// under protanopia and 20.6 under tritanopia. Direction is *also* carried by
// bar direction and a signed label, so colour is never the only cue.
//
// Status colours are reserved. They never double as a series hue, and they
// always ship with a label — `weather.band` renders text, not just a swatch.

export interface VizPalette {
  /** Revenue moved down — losses, shrinkage, erosion. */
  loss: string;
  /** Revenue moved up — growth, new, recovered. */
  gain: string;
  /** No material movement. Recedes toward the surface on purpose. */
  neutral: string;
  /** A third accent for a non-signed dimension (evidence, volume). */
  accent: string;
  /** Axis, grid and rule ink — recessive by design. */
  rule: string;
  surface: string;
}

const LIGHT: VizPalette = {
  loss: "#b0473d",
  gain: "#2f6ba8",
  neutral: "#9a9a9d",
  accent: "#9a5f14",
  rule: "color-mix(in srgb, #1d1f20 14%, transparent)",
  surface: "#ffffff",
};

const DARK: VizPalette = {
  loss: "#d3766b",
  gain: "#5e94d2",
  neutral: "#7c7c80",
  accent: "#ad8636",
  rule: "color-mix(in srgb, #e3e7ea 16%, transparent)",
  surface: "#1b2025",
};

/** Weather / health bands. Reserved — never reused as a series colour. */
export const BAND_COLOR: Record<string, string> = {
  GOOD: "var(--viz-gain)",
  FAIR: "var(--viz-accent)",
  POOR: "var(--viz-loss)",
  UNKNOWN: "var(--viz-neutral)",
};

/** Which way a flow bucket points. Drives both colour and bar direction, so the
 *  sign survives greyscale, forced colours and a printed page. */
export const BUCKET_SIGN: Record<string, -1 | 0 | 1> = {
  LOST: -1,
  SHRUNK: -1,
  STABLE: 0,
  GROWN: 1,
  RECOVERED: 1,
  NEW: 1,
};

export const BUCKET_LABEL: Record<string, string> = {
  LOST: "Lost",
  SHRUNK: "Spent less",
  STABLE: "Unchanged",
  GROWN: "Spent more",
  RECOVERED: "Came back",
  NEW: "New",
};

/** What each state actually means, for the tooltip and the legend.
 *
 *  "Spent less" and "Lost" are both red and both bad, and a reader looking at
 *  two red bands stacked has no way to tell which is which or why it matters
 *  that they are separate. They are separate because they need different
 *  phone calls. */
export const BUCKET_MEANING: Record<string, string> = {
  NEW: "First order ever — no trading history before this month",
  RECOVERED: "Traded before, went quiet, ordered again this month",
  GROWN: "Spent more this month than last",
  STABLE: "Spent about the same as last month",
  SHRUNK: "Spent less this month than last, but still ordering",
  LOST: "Ordered last month, nothing at all this month",
};

/** Six states drawn in three hues, separated by lightness within each hue.
 *
 *  The hue carries the direction — better, same, worse — which is the thing a
 *  glance needs. Lightness separates the states inside a direction, which is
 *  the thing a second look needs. Six *hues* would say these are six unrelated
 *  categories and lose the direction entirely, and the palette check fails
 *  above five anyway. Same reasoning as `CONFIDENCE_OPACITY` below. */
export const BUCKET_SHADE: Record<string, number> = {
  NEW: 1,
  RECOVERED: 0.72,
  GROWN: 0.48,
  STABLE: 1,
  SHRUNK: 0.62,
  LOST: 1,
};

/** Confidence is ordinal, so it gets one hue at three opacities plus a shape —
 *  never three hues, which would read as three unrelated categories. */
export const CONFIDENCE_OPACITY: Record<string, number> = {
  SUFFICIENT: 1,
  PARTIAL: 0.62,
  INSUFFICIENT: 0.34,
};

export const CONFIDENCE_LABEL: Record<string, string> = {
  SUFFICIENT: "Strong evidence",
  PARTIAL: "Partial evidence",
  INSUFFICIENT: "Thin evidence",
};

export function palette(dark: boolean): VizPalette {
  return dark ? DARK : LIGHT;
}

/** Emit the palette as CSS custom properties so SVG marks can reference them
 *  and a theme flip is one class change rather than a re-render. */
export function paletteVars(dark: boolean): Record<string, string> {
  const p = palette(dark);
  return {
    "--viz-loss": p.loss,
    "--viz-gain": p.gain,
    "--viz-neutral": p.neutral,
    "--viz-accent": p.accent,
    "--viz-rule": p.rule,
    "--viz-surface": p.surface,
  };
}
