/** The Industry design system, expressed once, for both consumers.
 *
 * This file exists because the look had two owners. The palette, the type ramp
 * and the shadow scale lived as CSS custom properties in `styles.css`, and
 * every MUI component would have needed the same values a second time in a
 * theme object. Two copies of a hex code diverge on the first tweak, and the
 * failure is silent: half the surfaces move and half do not.
 *
 * So the tokens below are the source, the MUI theme is built from them, and the
 * `:root` custom properties are *emitted* from the same tokens by `CssBaseline`
 * (see `cssVariables` at the bottom). `styles.css` keeps referring to
 * `var(--color-accent)` exactly as it did — it simply no longer declares it.
 *
 * The palette itself is unchanged from the original artifact. This is not a
 * re-skin: a distributor's staff have learned what the accent blue means on the
 * quote screen, and moving it to make a component library feel at home would
 * cost more than it buys. What MUI is here for is the behaviour that was being
 * hand-rolled — focus rings, dialogs that trap focus, menus that close on
 * Escape, controls that stay legible at 320px.
 */
import { createTheme } from "@mui/material/styles";

/* ── tokens ──────────────────────────────────────────────────────────────── */

export const tokens = {
  bg: "#f2f2f3",
  surface: "#e9e9ea",
  text: "#1d1f20",
  /* Darkened from #5980a6 on 2026-09 because it did not pass, and the failure
   * was on the product's most important control rather than somewhere quiet.
   * Measured in the running app: #5980a6 against the page ground is **3.71:1**,
   * and the six failing strings were all this one colour at 12.5–14px/600 —
   * "Create quote" among them, which is the button that sends a quotation into
   * a customer's books.
   *
   * The number that matters is small text, which needs 4.5:1 (WCAG 1.4.3); the
   * 3:1 large-text allowance does not apply to any of the six. #4a6e90 is the
   * smallest step down the same hue that clears it in both directions — 4.78:1
   * as ink on the ground, 5.35:1 as ground under white ink — so a chip reads
   * whether the accent is behind the text or in it. Darker candidates were
   * available and rejected: this is a recogniser for the brand, not a
   * contrast exercise, and #416180 (5.78:1) is visibly navy.
   *
   * Anything added to the `accents` ramp below should be checked the same way
   * rather than eyeballed: 500 and 600 are lighter than this and are not safe
   * for small text on the ground. */
  accent: "#4a6e90",
  accent2: "#728fab",

  neutral: {
    100: "#f5f5f8", 200: "#e7e7ea", 300: "#d4d4d7", 400: "#b7b7ba",
    500: "#98989b", 600: "#7a7a7d", 700: "#5d5d60", 800: "#424244",
    900: "#2b2b2d",
  },
  accents: {
    100: "#eef6ff", 200: "#d6ebff", 300: "#b5d9fd", 400: "#94bce3",
    500: "#749dc4", 600: "#597ea3", 700: "#416180", 800: "#2c455d",
    900: "#1d2d3d",
  },

  /* Darkened from #b7791f for the same reason and by the same measurement:
   * 3.35:1 on the panel ground, against 4.5:1 for the small text it is used
   * on ("Choose customer" is the one that showed up). #946014 is 4.90:1 on a
   * panel and 4.76:1 on the page. It is still amber; `.warn` is a word plus a
   * left border in every place it renders, so the hue was never carrying the
   * meaning alone — it just could not be read. */
  warn: "#946014",
  dangerBg: "#f6e6e3",
  dangerFg: "#b0473d",
  cautionBg: "#f7ecd6",
  cautionFg: "#6b4f16",

  fontHeading: '"Barlow Condensed", system-ui, sans-serif',
  fontBody: '"Barlow", system-ui, sans-serif',
  fontMono: 'ui-monospace, SFMono-Regular, Menlo, monospace',
  headingWeight: 600,

  /* The 3.4px base is inherited from the artifact and is deliberately odd —
     it makes the industrial drawing proportions land. Kept rather than
     rounded to MUI's 8px, because rounding it re-spaces every existing
     screen for no gain the user would notice as an improvement. */
  space: [0, 3.4, 6.8, 10.2, 13.6, 17, 20.4, 23.8, 27.2],

  radius: { sm: 2, md: 4, lg: 7 },

  shadow: {
    sm: "0 1px 2px rgba(43, 43, 45, 0.14)",
    md: "0 3px 10px rgba(43, 43, 45, 0.16)",
    lg: "0 12px 32px rgba(43, 43, 45, 0.22)",
  },
} as const;

/** Text at a stated opacity over the page. Spelled out rather than
 *  `color-mix` so the same value is available to JS and to CSS. */
const fade = (pct: number) => `rgba(29, 31, 32, ${pct / 100})`;

export const CSS_VARS: Record<string, string> = {
  "--color-bg": tokens.bg,
  "--color-surface": tokens.surface,
  "--color-text": tokens.text,
  "--color-accent": tokens.accent,
  "--color-accent-2": tokens.accent2,
  "--color-divider": fade(16),
  /* Muted ink for a caption, a unit, an axis label. It was read in six places
     across the chart stylesheet and declared in none, and the two failure modes
     differed by property: `color` is inherited, so those sites quietly took the
     body text colour, while `fill`'s initial value is black, so the SVG labels
     rendered black rather than muted. Declared here rather than patched at each
     call site, because a literal in a component is a value that will not
     follow. */
  "--color-text-secondary": tokens.neutral[700],

  ...Object.fromEntries(
    Object.entries(tokens.neutral).map(([k, v]) => [`--color-neutral-${k}`, v]),
  ),
  ...Object.fromEntries(
    Object.entries(tokens.accents).map(([k, v]) => [`--color-accent-${k}`, v]),
  ),

  "--warn": tokens.warn,
  "--danger-bg": tokens.dangerBg,
  "--danger-fg": tokens.dangerFg,
  "--caution-bg": tokens.cautionBg,
  "--caution-fg": tokens.cautionFg,

  "--font-heading": tokens.fontHeading,
  "--font-heading-weight": String(tokens.headingWeight),
  "--font-body": tokens.fontBody,

  ...Object.fromEntries(
    tokens.space.map((v, i) => [`--space-${i}`, `${v}px`]),
  ),

  "--radius-sm": `${tokens.radius.sm}px`,
  "--radius-md": `${tokens.radius.md}px`,
  "--radius-lg": `${tokens.radius.lg}px`,

  "--shadow-sm": tokens.shadow.sm,
  "--shadow-md": tokens.shadow.md,
  "--shadow-lg": tokens.shadow.lg,
};

/* ── the theme ───────────────────────────────────────────────────────────── */

const heading = (size: number, spacing = "-0.015em") => ({
  fontFamily: tokens.fontHeading,
  fontWeight: tokens.headingWeight,
  lineHeight: 1.12,
  letterSpacing: spacing,
  fontSize: size,
});

/* MUI wants 25 elevations. The artifact defined three, and the ramp between
   them is what a component library actually reaches for, so interpolate rather
   than repeating `shadow.md` twenty times — a flat ramp makes a Menu sitting
   over a Dialog indistinguishable from the Dialog. */
const shadows = Array.from({ length: 25 }, (_, i) => {
  if (i === 0) return "none";
  if (i <= 2) return tokens.shadow.sm;
  if (i <= 8) return tokens.shadow.md;
  return tokens.shadow.lg;
}) as unknown as import("@mui/material/styles").Theme["shadows"];

export const theme = createTheme({
  palette: {
    mode: "light",
    background: {
      default: tokens.bg,
      // Matches `--color-neutral-100`, which is what every hand-written card in
      // `styles.css` already uses. A whiter paper would look better in
      // isolation and would make MUI surfaces visibly disagree with the ones
      // not yet migrated, which is worse.
      paper: tokens.neutral[100],
    },
    primary: {
      main: tokens.accent,
      light: tokens.accents[400],
      dark: tokens.accents[700],
      contrastText: tokens.bg,
    },
    secondary: { main: tokens.accent2, contrastText: tokens.bg },
    error: { main: tokens.dangerFg, light: tokens.dangerBg, contrastText: "#fff" },
    warning: { main: tokens.warn, light: tokens.cautionBg, dark: tokens.cautionFg },
    info: { main: tokens.accents[600], light: tokens.accents[100] },
    success: { main: "#3f7d58" },
    text: {
      primary: tokens.text,
      secondary: fade(62),
      disabled: fade(38),
    },
    divider: fade(16),
    grey: tokens.neutral,
  },

  typography: {
    fontFamily: tokens.fontBody,
    fontSize: 15,
    htmlFontSize: 16,
    h1: heading(34),
    h2: heading(26),
    h3: heading(21),
    h4: heading(18),
    h5: heading(15),
    h6: {
      fontFamily: tokens.fontHeading,
      fontWeight: tokens.headingWeight,
      fontSize: 13,
      letterSpacing: "0.08em",
      textTransform: "uppercase",
      lineHeight: 1.3,
    },
    subtitle1: { fontSize: 14, lineHeight: 1.5 },
    subtitle2: {
      fontFamily: tokens.fontHeading, fontWeight: 600, fontSize: 12,
      letterSpacing: "0.06em", textTransform: "uppercase",
    },
    body1: { fontSize: 15, lineHeight: 1.55 },
    body2: { fontSize: 13, lineHeight: 1.5 },
    caption: { fontSize: 11.5, lineHeight: 1.45 },
    overline: {
      fontFamily: tokens.fontHeading, fontWeight: 600, fontSize: 11,
      letterSpacing: "0.08em", textTransform: "uppercase", lineHeight: 1.4,
    },
    button: {
      fontFamily: tokens.fontHeading,
      fontWeight: tokens.headingWeight,
      fontSize: 14,
      letterSpacing: "0.01em",
      // Not uppercase. These labels are sentences the platform means literally
      // — "Selling below cost needs an owner" shouted reads as an alarm.
      textTransform: "none",
    },
  },

  shape: { borderRadius: tokens.radius.md },
  shadows,
  spacing: (n: number) => `${n * 6.8}px`,

  components: {
    /* The keyboard focus ring, put where it actually wins.
     *
     * `styles.css` has declared `:focus-visible { outline: 2px solid }` for a
     * long time and it has never rendered on a single control. MUI's own
     * `MuiButtonBase-root { outline: 0 }` is emitted into the document after
     * the stylesheet at equal specificity, so source order decides and MUI
     * takes it. The tell is that `outline-offset: 2px` survives on every
     * button: the rule matched, and only the outline was overridden.
     *
     * Measured before this fix: 8 of 45 tab stops on the Quote Builder showed
     * a ring, and all 8 were AG Grid header cells — the only focusable things
     * on the screen MUI does not own. Every button fell back to MUI's pulsing
     * ripple, which is an animation rather than an indicator and is not
     * guarded by `prefers-reduced-motion`.
     *
     * Declaring it against `.Mui-focusVisible` puts it inside MUI's cascade
     * instead of fighting it, and covers Button, IconButton, ListItemButton,
     * ToggleButton and clickable Chip at once. `:focus-visible` rather than
     * `:focus` throughout, so a mouse press does not draw it. */
    MuiButtonBase: {
      styleOverrides: {
        root: {
          "&.Mui-focusVisible": {
            outline: `2px solid ${tokens.accent}`,
            outlineOffset: 2,
          },
        },
      },
    },

    MuiCssBaseline: {
      styleOverrides: {
        ":root": CSS_VARS,
        body: {
          background: tokens.bg,
          color: tokens.text,
          fontFamily: tokens.fontBody,
          fontSize: 15,
          lineHeight: 1.55,
          WebkitFontSmoothing: "antialiased",
        },
        // Long numbers in a column only line up with tabular figures, and
        // "₹1,11,111" against "₹2,40,961" is the whole point of the screen.
        ".num, td.num, .mono": { fontVariantNumeric: "tabular-nums" },
        "::selection": { background: "rgba(89, 128, 166, 0.3)" },
      },
    },

    MuiPaper: {
      defaultProps: { elevation: 0 },
      styleOverrides: {
        root: { backgroundImage: "none" },
        outlined: { borderColor: fade(16) },
      },
    },

    MuiButton: {
      defaultProps: { disableElevation: true },
      styleOverrides: {
        root: { borderRadius: tokens.radius.md, paddingInline: 12, minHeight: 34 },
        sizeSmall: { fontSize: 12.5, minHeight: 28, paddingInline: 9 },
      },
    },

    MuiToggleButton: {
      styleOverrides: {
        root: {
          fontFamily: tokens.fontHeading, fontWeight: 600, fontSize: 12.5,
          textTransform: "none", paddingInline: 12,
          "&.Mui-selected": {
            background: tokens.accents[100],
            color: tokens.accents[800],
            borderColor: tokens.accent,
          },
        },
      },
    },

    MuiChip: {
      styleOverrides: {
        root: { fontFamily: tokens.fontHeading, fontWeight: 600, letterSpacing: "0.03em" },
        sizeSmall: { height: 21, fontSize: 11 },
        label: { paddingInline: 7 },
      },
    },

    MuiTextField: { defaultProps: { size: "small", variant: "outlined" } },
    MuiSelect: { defaultProps: { size: "small" } },

    MuiOutlinedInput: {
      styleOverrides: {
        /* 14px on a desk, 16px on a phone, and the 16 is not a taste decision.
         * iOS Safari zooms the viewport when a text field under 16px takes
         * focus, and it does not zoom back out — so on the one screen this
         * product is used standing up, tapping the rate field threw the layout
         * off and left the salesperson pinching to find the grid again. Every
         * field in the app was 14px, this one included.
         *
         * Scoped to the phone rather than raised everywhere: 14px is the right
         * density at a desk, and this is a mobile-browser behaviour, not a
         * legibility one. Below `sm`, which is where the drawer is temporary
         * and the grid has already become cards. */
        root: {
          background: tokens.surface,
          borderRadius: tokens.radius.md,
          fontSize: 16,
          "@media (min-width:600px)": { fontSize: 14 },
        },
        notchedOutline: { borderColor: fade(16) },
        input: { paddingBlock: 7 },
      },
    },

    MuiInputLabel: {
      styleOverrides: {
        root: { fontSize: 13, color: fade(70) },
        shrink: { fontSize: 14 },
      },
    },

    MuiFormControlLabel: {
      styleOverrides: { label: { fontSize: 14 } },
    },

    MuiTooltip: {
      defaultProps: { arrow: true, enterDelay: 200 },
      styleOverrides: {
        tooltip: {
          background: tokens.neutral[900],
          fontSize: 12.5,
          lineHeight: 1.5,
          fontWeight: 400,
          maxWidth: 320,
          padding: "8px 11px",
          borderRadius: tokens.radius.md,
        },
        arrow: { color: tokens.neutral[900] },
      },
    },

    MuiDialog: {
      styleOverrides: {
        paper: {
          background: tokens.bg,
          border: `1px solid ${fade(16)}`,
          borderRadius: tokens.radius.lg,
        },
      },
    },
    MuiDialogTitle: {
      styleOverrides: {
        root: {
          fontFamily: tokens.fontHeading, fontWeight: 600, fontSize: 21,
          borderBottom: `1px solid ${fade(16)}`, padding: "13.6px 20.4px",
          // DialogTitle renders with the `h6` variant internally, and in this
          // ramp `h6` is the uppercase eyebrow style. Without these two lines
          // every dialog shouts its own title: "DISMISS THIS DECISION".
          textTransform: "none",
          letterSpacing: "-0.015em",
        },
      },
    },
    MuiDialogActions: {
      styleOverrides: {
        root: { borderTop: `1px solid ${fade(16)}`, padding: "10.2px 20.4px", gap: 10.2 },
      },
    },

    MuiDrawer: {
      styleOverrides: { paper: { backgroundImage: "none", borderColor: fade(16) } },
    },

    MuiTableCell: {
      styleOverrides: {
        root: { borderBottomColor: fade(8), padding: "7px 8px", fontSize: 13 },
        head: {
          fontFamily: tokens.fontHeading, fontWeight: 600, fontSize: 11,
          letterSpacing: "0.04em", textTransform: "uppercase",
          color: fade(55), borderBottomColor: fade(16),
        },
      },
    },

    MuiTab: {
      styleOverrides: {
        root: {
          fontFamily: tokens.fontHeading, fontWeight: 600, fontSize: 13.5,
          textTransform: "none", minHeight: 42,
        },
      },
    },

    MuiAlert: {
      styleOverrides: {
        root: { borderRadius: tokens.radius.md, fontSize: 13, alignItems: "center" },
      },
      // v9 dropped the `standardWarning`-style compound keys in favour of
      // `variants`. The colours are the artifact's own caution/danger pairs —
      // MUI's defaults are a brighter, more alarming red than this palette
      // uses, and on a screen where a real policy breach is also red, a
      // routine info banner in the same red flattens the distinction.
      variants: [
        { props: { variant: "standard", severity: "warning" as const },
          style: { background: tokens.cautionBg, color: tokens.cautionFg } },
        { props: { variant: "standard", severity: "error" as const },
          style: { background: tokens.dangerBg, color: tokens.dangerFg } },
        { props: { variant: "standard", severity: "info" as const },
          style: { background: tokens.accents[100], color: tokens.accents[800] } },
      ],
    },

    MuiLink: { defaultProps: { underline: "hover" } },

    MuiListItemButton: {
      styleOverrides: {
        root: {
          borderRadius: tokens.radius.md,
          "&.Mui-selected": {
            background: tokens.accents[100],
            color: tokens.accents[800],
            "&:hover": { background: tokens.accents[200] },
          },
        },
      },
    },
  },
});

export default theme;
