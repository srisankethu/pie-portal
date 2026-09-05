// The shared interface vocabulary. One answer per question, used everywhere.
//
// `docs/ui-standards.md` is the standard; this file is the part of it that
// compiles. Everything here is Material UI underneath and reads the theme for
// every colour, space and radius — a literal in a component is a value that
// will not follow when the palette changes, and this product has been re-skinned
// once already.
//
// **Built from what was already here, not beside it.** Each component below
// names the hand-rolled thing it replaces, and the call sites move over as
// screens are touched. A second vocabulary sitting next to the first is how a
// design system becomes two design systems.
//
// **Status is never colour alone.** A `Chip` carries a shape and a word as well
// as a hue; coloured text carries only the hue, which is unreadable to a
// substantial minority of people, meaningless in greyscale print and gone under
// forced-colours mode. Charts are the exception and they are a deliberate one:
// a bar's colour is an *encoding* with a legend, not a status.

import { Children } from "react";
import type { ElementType, ReactNode } from "react";
import { Link as RouterLink } from "react-router-dom";
import type { SxProps, Theme } from "@mui/material/styles";

import { formatDateTime } from "../when";
import type { HumanAction, HumanActionEntry } from "./types";
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Avatar from "@mui/material/Avatar";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Grid from "@mui/material/Grid";
import IconButton from "@mui/material/IconButton";
import Link from "@mui/material/Link";
import Paper from "@mui/material/Paper";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import ArrowUpward from "@mui/icons-material/ArrowUpward";
import ArrowDownward from "@mui/icons-material/ArrowDownward";
import RemoveIcon from "@mui/icons-material/Remove";
import CloseIcon from "@mui/icons-material/Close";

import { money } from "../money";
import { tokens } from "../theme";
import { Tip } from "../Tip";

// ── typography ───────────────────────────────────────────────────────────────
//
// One ramp: page title → section header → widget title → label → value →
// secondary metadata. Screens used to reach for whatever `<h1>`/`<h3>` looked
// right, which is how three different section headings ended up on three
// screens that do the same job.

/** A page or section heading, with the question it answers underneath.
 *
 *  Replaces `.dp-head` and `.section-h`. `level` picks the rung on the ramp;
 *  `actions` is whatever belongs on the same line, right-aligned. */
export function SectionHeader({
  title, sub, tip, badge, actions, level = "page",
}: {
  title: ReactNode;
  /** What this screen or section answers. Every view in this product answers
   *  one — writing it down keeps sections from accumulating that answer none. */
  sub?: ReactNode;
  /** ReactNode rather than string: AdminScreens' "People and roles" tip carries
   *  a `<b>`, and `Tip`'s own `text` has always accepted a node — the narrower
   *  type here was the only thing keeping that section on the old `Labelled`. */
  tip?: ReactNode;
  /** A state word belonging to the title, rendered immediately after it.
   *
   *  Distinct from `actions`, which is right-aligned beside the buttons: three
   *  headers hand-rolled their own row because a `StatusChip` in `actions` ends
   *  up at the far edge, where it reads as a control rather than as part of the
   *  name it qualifies. */
  badge?: ReactNode;
  actions?: ReactNode;
  level?: "page" | "section" | "widget";
}) {
  const variant = level === "page" ? "h1" : level === "section" ? "h2" : "h3";
  return (
    <Stack
      direction="row"
      spacing={2}
      useFlexGap
      sx={{
        mb: level === "page" ? 3 : 2,
        flexWrap: "wrap", rowGap: 1,
        alignItems: "flex-start", justifyContent: "space-between",
      }}
    >
      <Box sx={{ minWidth: 0 }}>
        <Typography
          variant={variant}
          sx={{
            mb: sub ? 0.5 : 0,
            display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap",
          }}
        >
          {title}
          {tip ? <Tip text={tip} /> : null}
          {badge}
        </Typography>
        {sub && (
          <Typography variant="body2" color="text.secondary" sx={{ maxWidth: "68ch" }}>
            {sub}
          </Typography>
        )}
      </Box>
      {actions && (
        <Stack
          direction="row"
          spacing={1}
          useFlexGap
          sx={{ alignItems: "center", flexWrap: "wrap", rowGap: 1 }}
        >
          {actions}
        </Stack>
      )}
    </Stack>
  );
}

/** A panel's own mark: the 11–12px uppercase label above its content.
 *
 *  The ramp's smallest heading is `h3` at 21px, so a panel wanting a micro
 *  label had nowhere to go and four places built one out of `variant="overline"`
 *  by hand — three of them in `ui.tsx` alone, plus `.facts-mark` in styles.css.
 *  This is that rung, and it is deliberately not a `SectionHeader` level: those
 *  emit real headings, and a mark is a label, not a document outline entry. */
export function PanelMark({
  children, mark, sx,
}: {
  children: ReactNode;
  /** The square before the label, and its tint. This is the vocabulary that
   *  separates "a model wrote this" (accent) from "this is arithmetic" (ink) —
   *  it was `.facts-mark::before` and an inline square in `ui.tsx`, which is
   *  §10's threshold of two. */
  mark?: "accent" | "ink";
  sx?: SxProps<Theme>;
}) {
  return (
    <Typography
      variant="overline"
      color={mark === "ink" ? "text.primary" : "text.secondary"}
      sx={{
        display: "flex", alignItems: "center", gap: 0.75,
        lineHeight: 1.4, ...sx,
      }}
    >
      {mark && (
        <Box
          component="span"
          aria-hidden
          sx={{
            width: tokens.mark, height: tokens.mark, flexShrink: 0,
            bgcolor: mark === "ink" ? "text.primary" : tokens.accents[700],
          }}
        />
      )}
      {children}
    </Typography>
  );
}

/** The muted second line under a value — a code, a source, a timestamp.
 *
 *  Replaces `className="fsrc"` and three local `Meta` copies written in one
 *  afternoon by three agents who could not edit this file. The class version
 *  was only ever declared under three ancestors, so most of its 33 call sites
 *  rendered at body size in body ink; a component cannot be scoped out that way.
 */
export function Meta({
  children, inline = false, sx,
}: {
  children: ReactNode;
  /** Beside the value rather than under it — a size after a filename, "of 500
   *  rows" after a count. Three call sites were overriding `display` by hand. */
  inline?: boolean;
  sx?: SxProps<Theme>;
}) {
  return (
    <Typography
      variant="caption"
      color="text.secondary"
      component={inline ? "span" : "div"}
      sx={{ display: inline ? "inline" : "block", lineHeight: 1.45, ...sx }}
    >
      {children}
    </Typography>
  );
}

/** A field's label, with the optional why beside it.
 *
 *  The overline-plus-`Tip` pair, which two screens had written out identically.
 *  Not a `<label>`: it captions a rendered value, and MUI's own form controls
 *  bring their own label for anything a person types into. */
export function FieldLabel({
  children, tip, sx,
}: { children: ReactNode; tip?: ReactNode; sx?: SxProps<Theme> }) {
  return (
    <Typography
      variant="overline"
      color="text.secondary"
      sx={{ display: "flex", alignItems: "center", gap: 0.5, lineHeight: 1.4, ...sx }}
    >
      {children}
      {tip ? <Tip text={tip} /> : null}
    </Typography>
  );
}

// ── values ───────────────────────────────────────────────────────────────────

/** Money. Tabular figures, so a column of them lines up.
 *
 *  A component rather than a bare `money()` call so the alignment and the
 *  optional sign are decided once. Columns of rupees that do not line up are
 *  columns nobody adds up by eye. */
export function CurrencyValue({
  value, signed = false, bold = false, sx,
}: {
  /** A string is not a mistake here. Decision money is serialized as a decimal
   *  *string* (`DecisionImpact.financial`, `DecisionRanking.financial`) so the
   *  server's `Decimal` never round-trips through a float; `money()` has always
   *  taken both. Narrowing this to `number` is what kept ImpactPanel,
   *  RankingPanel and DecisionCard on a bare `money()` call. */
  value: number | string | null | undefined;
  /** Show an explicit + for a positive. For a movement, not for a balance. */
  signed?: boolean;
  bold?: boolean;
  sx?: object;
}) {
  const n = typeof value === "string" ? Number(value) : value;
  // Not `== null`: a string that is not a number must read as "not known"
  // rather than render "NaN", which is what a bare `money()` would have done.
  if (n == null || !Number.isFinite(n)) return <Box component="span" sx={sx}>—</Box>;
  const sign = signed ? (n >= 0 ? "+" : "−") : n < 0 ? "−" : "";
  return (
    <Box
      component="span"
      sx={{
        fontVariantNumeric: "tabular-nums",
        fontWeight: bold ? 600 : "inherit",
        whiteSpace: "nowrap",
        ...sx,
      }}
    >
      {sign}{money(Math.abs(n))}
    </Box>
  );
}

/** A ratio as a percentage, with an em dash for "not known".
 *
 *  `null` is not zero. A margin nobody could compute and a margin of nought are
 *  different facts, and the second one is a much worse problem. */
export function PercentageValue({
  value, digits = 1, sx,
}: { value: number | null | undefined; digits?: number; sx?: object }) {
  return (
    <Box component="span" sx={{ fontVariantNumeric: "tabular-nums", ...sx }}>
      {value == null ? "—" : `${(value * 100).toFixed(digits)}%`}
    </Box>
  );
}

/** A movement: arrow, figure, and a word for what direction means here.
 *
 *  Replaces `.wf-row-value.pos/.neg` and `.story-hero-value.up/.down`, which
 *  were colour and nothing else. The arrow survives greyscale; `label` carries
 *  the meaning, because up is good for revenue and bad for days-to-pay and the
 *  component cannot know which. */
export function VarianceIndicator({
  value, label, format = "currency", digits = 1, invert = false, sx,
}: {
  value: number | null | undefined;
  /** e.g. "against last quarter". Optional, and worth writing. */
  label?: ReactNode;
  format?: "currency" | "percent" | "number";
  digits?: number;
  /** True when down is the good direction — days late, cost, idle stock. */
  invert?: boolean;
  /** For the display-sized heroes, which pass `fontSize: "inherit"` so the
   *  surrounding type scale wins. The arrow already inherits. */
  sx?: object;
}) {
  if (value == null) return <Typography variant="body2" color="text.secondary">—</Typography>;
  const flat = value === 0;
  const good = invert ? value < 0 : value > 0;
  const Icon = flat ? RemoveIcon : value > 0 ? ArrowUpward : ArrowDownward;
  return (
    <Stack
      direction="row" spacing={0.5} component="span"
      sx={{ alignItems: "center", display: "inline-flex" }}
    >
      <Icon
        fontSize="inherit"
        aria-hidden
        sx={{ color: flat ? "text.disabled" : good ? "success.main" : "error.main" }}
      />
      <Box
        component="span"
        sx={{
          fontVariantNumeric: "tabular-nums",
          fontWeight: 600,
          color: flat ? "text.secondary" : good ? "success.main" : "error.main",
          ...sx,
        }}
      >
        {format === "currency"
          ? money(Math.abs(value))
          : format === "percent"
            ? `${(Math.abs(value) * 100).toFixed(digits)}%`
            : Math.abs(value).toLocaleString("en-IN")}
      </Box>
      {label && (
        <Typography variant="caption" color="text.secondary">{label}</Typography>
      )}
    </Stack>
  );
}

/** A name inside a sentence or a chart that opens the thing it names.
 *
 *  Replaces the `.link-btn` buttons in `viz.css`. They were `<button>`s dressed
 *  as links, and every one of them had to re-declare its own underline, colour
 *  and focus ring, with the disabled state a second class
 *  (`.multiple-name:disabled`) that only one of the five had. This brings the
 *  rest from the theme.
 *
 *  **`to` for somewhere, `onClick` for something.** The original said a button
 *  was "correct semantics for something that navigates within a SPA". That was
 *  true of the SPA it was written for and stopped being true when `route.ts`
 *  landed: this application has real URLs now, and a destination reachable only
 *  by a click handler is a destination the browser does not know about. No
 *  ctrl-click, no middle-click, no "open in a new tab", no destination on
 *  hover, and announced to a screen reader as a button. That is the whole of
 *  what `route.ts` says the router migration was for — "**nothing was a
 *  link**" — and the nav bar got it while every name inside a screen did not.
 *
 *  So a call site that goes somewhere passes `to` and renders an `<a href>`;
 *  one that does something here — Close, reset, Show everything — passes
 *  `onClick` and stays a `<button>`, which is what it should have been all
 *  along. The union below makes "neither" a type error rather than a dead
 *  control that looks alive.
 *
 *  `disabled` still wins over `to`: a name that cannot be opened must not be a
 *  link, because an anchor has no disabled state and the browser would follow
 *  it. */
type InlineLinkProps = {
  children: ReactNode;
  disabled?: boolean;
  /** For a name that heads its own row rather than sitting mid-sentence. */
  bold?: boolean;
  sx?: object;
} & (
  /** Where this goes: a path from `route.ts` — `vizPath()` or `pathFor()` —
   *  never a string written out here. `onClick` alongside it is for what else
   *  the press does (closing the panel it sits in); the navigation is the
   *  anchor's, not the handler's. */
  | { to: string; onClick?: () => void }
  /** Or what it does, when it does not leave the screen. */
  | { to?: undefined; onClick: () => void }
);

export function InlineLink({
  children, to, onClick, disabled = false, bold = false, sx,
}: InlineLinkProps) {
  const style: SxProps<Theme> = {
    font: "inherit",
    textAlign: "left",
    minWidth: 0,
    // A `<button>` is `vertical-align: middle` by default, which lifts the
    // name off the baseline of the amount sitting next to it. The anchor
    // branch keeps it so the two read identically in a row of figures.
    verticalAlign: "baseline",
    fontWeight: bold ? 600 : undefined,
    // A disabled name is still worth reading — it is a customer who cannot
    // be opened, not an absent one.
    color: disabled ? "text.secondary" : undefined,
    cursor: disabled ? "default" : "pointer",
    ...sx,
  };

  if (to !== undefined && !disabled) {
    return (
      <Link
        component={RouterLink}
        to={to}
        variant="body2"
        underline="always"
        onClick={onClick}
        sx={style}
      >
        {children}
      </Link>
    );
  }
  return (
    <Link
      component="button"
      type="button"
      variant="body2"
      underline={disabled ? "none" : "always"}
      disabled={disabled}
      onClick={onClick}
      sx={style}
    >
      {children}
    </Link>
  );
}

// ── status ───────────────────────────────────────────────────────────────────

export type Tone = "neutral" | "good" | "warn" | "bad" | "info";

const TONE_COLOR: Record<Tone, "default" | "success" | "warning" | "error" | "info"> = {
  neutral: "default", good: "success", warn: "warning", bad: "error", info: "info",
};

/** Any state word: sync result, approval, evidence level, connection health.
 *
 *  One component so a paused connection and a dismissed decision do not end up
 *  described in two visual languages. `tip` is where the *meaning* goes — a
 *  badge reading "PARTIAL" that cannot say what was partial is decoration. */
export function StatusChip({
  label, tone = "neutral", tip, size = "small", icon, dense = false,
}: {
  label: ReactNode;
  tone?: Tone;
  tip?: string;
  size?: "small" | "medium";
  icon?: React.ReactElement;
  /** Tighter, for several chips inside one grid cell. A row of default chips
   *  under a product code costs more vertical space than the code itself. */
  dense?: boolean;
}) {
  const chip = (
    <Chip
      label={label}
      color={TONE_COLOR[tone]}
      size={size}
      icon={icon}
      variant={tone === "neutral" ? "outlined" : "filled"}
      sx={{
        fontWeight: 600, letterSpacing: "0.02em",
        ...(dense
          ? { height: 18, fontSize: 10.5, "& .MuiChip-label": { px: 0.75 } }
          : null),
      }}
    />
  );
  return tip ? <Tooltip title={tip}>{chip}</Tooltip> : chip;
}

// ── touch ────────────────────────────────────────────────────────────────────

/** The smallest a control may be on a screen somebody works with a thumb.
 *
 *  44px is the figure WCAG 2.2 §2.5.8 and both platform guidelines settle on,
 *  and it is not a rounding of MUI's defaults: a `small` `Chip` is 24px high
 *  and a `small` `IconButton` about 30px, which is comfortable with a mouse and
 *  a coin-toss with a thumb in a machine shop. Spread rather than wrapped in a
 *  component, because the controls that need it are a chip, a button, an icon
 *  button and a text field — four MUI components with nothing else in common. */
export const TOUCH_TARGET = 44;

/** `sx` spread that enforces it. `minHeight`/`minWidth`, never fixed sizes, so
 *  a control whose content is already taller is left alone. */
export const TOUCH = {
  minHeight: TOUCH_TARGET,
  minWidth: TOUCH_TARGET,
} as const;

/** One choice in a row of them, with how many rows it would leave.
 *
 *  Three screens had written this out — the quote's line-state chips, the
 *  quote's margin-floor chip and the decision queue's type chips — as a `Chip`
 *  with a count in the `avatar` slot, a `color`/`variant` pair keyed on whether
 *  it is the current one, and an `onClick`. Identical in all three but for the
 *  labels, which is §10 exactly; and when the tap target needed to grow, it
 *  needed to grow in three places.
 *
 *  The count sits in the avatar slot rather than the label because a count
 *  baked into the label loses its contrast when the chip is filled. */
export function FilterChip({
  label, count, selected, onClick, alert = false, tone,
}: {
  label: string;
  count?: number;
  selected: boolean;
  onClick: () => void;
  /** A count worth seeing even when this chip is not the current one —
   *  unresolved lines and lines needing a decision stop a quote being sent. */
  alert?: boolean;
  /** Overrides the selected colour. `error` for a chip that selects a problem. */
  tone?: "primary" | "error";
}) {
  return (
    <Chip
      label={label}
      avatar={count === undefined ? undefined : (
        <Avatar sx={{
          bgcolor: "transparent", fontSize: 11, fontWeight: 700,
          color: alert && !selected ? "var(--danger-fg)" : undefined,
        }}>
          {count}
        </Avatar>
      )}
      color={selected ? (tone ?? "primary") : "default"}
      variant={selected ? "filled" : "outlined"}
      onClick={onClick}
      /* `alert` used to be a red digit and nothing else, which is §6's own
       * counter-example: a reader who loses the hue loses the fact that these
       * lines stop the quote going out. The ring restates it as a shape, so
       * the chip is distinguishable in greyscale and under forced colours. */
      sx={{
        ...TOUCH,
        borderRadius: 999,
        px: 0.5,
        ...(alert && !selected && {
          borderColor: "var(--danger-fg)",
          borderWidth: 2,
        }),
        /* Chip renders a ButtonBase only when `clickable`, and MUI infers that
         * from `onClick` — but it ships no ripple root here, so before this
         * the *only* thing keyboard focus changed was the background, by
         * 1.56:1 against WCAG 1.4.11's 3:1 floor for a non-text indicator.
         * Measured: every one of these was effectively unfocusable to look at.
         * The theme's `.Mui-focusVisible` ring covers ButtonBase generally;
         * this restates it so a Chip is right whether or not MUI decides to
         * give it one. */
        "&.Mui-focusVisible": {
          outline: "2px solid var(--color-accent)",
          outlineOffset: 2,
        },
      }}
    />
  );
}

const BAND_TONE: Record<string, Tone> = { HIGH: "bad", MEDIUM: "warn", LOW: "neutral" };

/** A decision's priority band. Replaces the `.pri` span.
 *
 *  The band, never the score: three HIGH cards in an arbitrary order is a list
 *  that does not answer "what first?", which is what the *sort* is for. */
export function PriorityChip({ band, tip }: { band: string; tip?: string }) {
  return <StatusChip label={band} tone={BAND_TONE[band] ?? "neutral"} tip={tip} />;
}

// ── the four states every view has ───────────────────────────────────────────
//
// Loading, empty, error and loaded. A dashboard shows a spinner and then a
// blank rectangle; a product says *why* it is blank and what would change it.

/** Shaped skeletons that reserve the height the content will take.
 *
 *  Replaces the `.skeleton` and `.viz-skeleton` divs. Reserving the height is
 *  the point — a page that grows when data lands makes somebody lose their
 *  place, and it is what a hand-rolled shimmer usually forgets. */
export function LoadingState({
  rows = 3, height = 44, label,
}: { rows?: number; height?: number; label?: string }) {
  return (
    <Stack spacing={1} role="status" aria-live="polite" aria-busy>
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} variant="rounded" height={height} />
      ))}
      {label && (
        <Typography variant="caption" color="text.secondary">{label}</Typography>
      )}
    </Stack>
  );
}

/** Nothing to show, and why. Replaces `.dp-empty`.
 *
 *  `reason` is the server's own words wherever there is a server: it knows the
 *  difference between "nothing synced yet", "nothing qualified against your
 *  threshold" and "you cannot see this", and a screen that repeats one generic
 *  sentence for all three is a screen nobody trusts. */
export function EmptyState({
  title = "Nothing to show yet", reason, action, flat = false,
}: {
  title?: ReactNode; reason?: ReactNode; action?: ReactNode;
  /** No surface of its own — for an empty state that stands *inside* a panel
   *  rather than in place of one. Without it a panel showing "nothing here"
   *  draws a second outlined box inside the first, which reads as a nested
   *  thing rather than as the panel's own content. */
  flat?: boolean;
}) {
  const body = (
    <>
      <Typography variant="subtitle1" sx={{ mb: reason ? 0.5 : 0 }}>{title}</Typography>
      {reason && (
        <Typography variant="body2" color="text.secondary" sx={{ maxWidth: "60ch", mx: "auto" }}>
          {reason}
        </Typography>
      )}
      {action && <Box sx={{ mt: 2 }}>{action}</Box>}
    </>
  );
  return flat
    ? <Box sx={{ p: 4, textAlign: "center" }}>{body}</Box>
    : <Paper variant="outlined" sx={{ p: 4, textAlign: "center" }}>{body}</Paper>;
}

// ── what a screen will not answer ────────────────────────────────────────────

/** The words for each `kind` the server stamps on a refusal.
 *
 *  The taxonomy is the server's (`commercial/insight/absence.py`); the wording
 *  is this file's, so there is one map rather than one on each side of the
 *  wire. `tone` is doing real work here — a limit and a job somebody could
 *  finish this week should not read the same, and until they carried a kind
 *  they did. */
const ABSENCE: Record<string, { label: string; tone: Tone; tip: string }> = {
  PERMANENT: {
    label: "Not answerable", tone: "neutral",
    tip: "No amount of extra data would answer this. Nothing to chase.",
  },
  COLLECTABLE: {
    label: "Needs data nobody records", tone: "warn",
    tip: "Answerable, once somebody records the missing field. This is a job.",
  },
  BUILDABLE: {
    label: "Not built yet", tone: "info",
    tip: "The data exists or can be bought; wiring it is engineering work.",
  },
  TRANSIENT: {
    label: "Too early", tone: "neutral",
    tip: "Resolves on its own as the period runs. Nothing to do.",
  },
  WITHHELD: {
    label: "Management information", tone: "neutral",
    tip: "Computed and correct, and not shown to your role.",
  },
};

/** What a screen says it cannot answer — rendered, never dropped.
 *
 *  Replaces six copies: three identical local `Unavailable` components (Mix,
 *  Dependency, Bonds — differing in one word) and three inline `<ul>` blocks
 *  (History, Screens, and the since-removed negotiation desk). They also read three different server shapes,
 *  which is why the normalising happens here: `{what, why}`, `{series, reason}`
 *  and `{scenario, needs, why}` all describe one thing, and unifying them at
 *  the render layer costs nothing where unifying them on the wire would break
 *  every reader at once.
 *
 *  `verb` is the only thing the call sites still differ on, because "not in the
 *  score" and "not claimed" genuinely say different things about a bond and
 *  about a grid. */
export function Unavailable({
  items, verb = "not shown",
}: {
  items: ReadonlyArray<Record<string, unknown>>;
  verb?: string;
}) {
  if (!items.length) return null;
  return (
    <Stack component="ul" spacing={0.75} className="tl-unavailable said-plain">
      {items.map((u, i) => {
        const raw = String(u.what ?? u.series ?? u.scenario ?? "");
        // `what` is already prose; `series`/`scenario` are identifiers.
        const title = u.what ? raw : raw.replace(/_/g, " ").toLowerCase();
        const body = String(u.why ?? u.reason ?? "");
        const needs = u.needs ? `Needs ${String(u.needs)}. ` : "";
        const kind = ABSENCE[String(u.kind ?? "")];
        return (
          <Box component="li" key={i}>
            <strong>{title}</strong> — {verb}.{" "}
            {kind && (
              <StatusChip label={kind.label} tone={kind.tone} tip={kind.tip} dense />
            )}{" "}
            <span className="viz-muted">{needs}{body}</span>
          </Box>
        );
      })}
    </Stack>
  );
}

/** It did not load. What went wrong, and the way back.
 *
 *  Replaces `LoadFailed` and the `.state-panel` blocks. This must never be
 *  mistaken for an empty state: "we could not look" is not "there is nothing to
 *  see", and the difference is what stops somebody being told everything is
 *  fine at exactly the moment the system knows nothing. */
export function ErrorState({
  title = "This did not load", error, onRetry, onClose, busy = false, sx,
}: {
  title?: ReactNode; error?: ReactNode; onRetry?: () => void;
  /** Dismissible, for one refused request standing beside controls that still
   *  work — as opposed to a screen that failed to load, which must not be
   *  dismissible because there is nothing behind it. Two screens wrote a local
   *  `ProblemAlert` for exactly this. */
  onClose?: () => void;
  busy?: boolean;
  sx?: SxProps<Theme>;
}) {
  return (
    <Alert
      severity="error"
      sx={sx}
      // Both go in `action`: MUI renders `action` *instead of* the close
      // button, so passing `onClose` alongside one would silently drop the X.
      action={(onRetry || onClose) ? (
        <Stack direction="row" spacing={0.5} sx={{ alignItems: "center" }}>
          {onRetry && (
            <Button color="inherit" size="small" onClick={onRetry} disabled={busy}>
              {busy ? "Retrying…" : "Try again"}
            </Button>
          )}
          {onClose && (
            <IconButton
              color="inherit" size="small" onClick={onClose} aria-label="Dismiss"
            >
              <CloseIcon fontSize="inherit" />
            </IconButton>
          )}
        </Stack>
      ) : undefined}
    >
      <AlertTitle>{title}</AlertTitle>
      {error}
    </Alert>
  );
}

// ── surfaces ─────────────────────────────────────────────────────────────────

/** One figure, its label, and optionally what it did.
 *
 *  A `Paper`, not a `Card`: a KPI tile is a widget, not a business entity you
 *  could open or act on. Replaces `.dp-count` and the hand-built stock KPI row.
 */
export function MetricCard({
  label, value, sub, variance, tip, action, unknown,
}: {
  label: ReactNode;
  /** The figure. Pass `null` and give `unknown` a reason when there is none —
   *  a tile showing 0 where nothing was measured is the benign default
   *  CLAUDE.md §1 rules out. */
  value: ReactNode;
  sub?: ReactNode;
  variance?: ReactNode;
  tip?: ReactNode;
  action?: ReactNode;
  /** Words in the figure's place, at a readable size rather than at `h3`.
   *  Two screens reached for a bare `fontSize: "0.6em"` to say "not measured"
   *  inside the figure slot; this is that, once. */
  unknown?: ReactNode;
}) {
  return (
    <Paper variant="outlined" sx={{ p: 2, height: "100%" }}>
      <Stack spacing={0.5}>
        <Typography
          variant="overline"
          color="text.secondary"
          sx={{ lineHeight: 1.4, display: "flex", alignItems: "center", gap: 0.5 }}
        >
          {label}
          {tip ? <Tip text={tip} /> : null}
        </Typography>
        {unknown ? (
          <Typography variant="body2" color="text.secondary" sx={{ py: 0.5 }}>
            {unknown}
          </Typography>
        ) : (
          <Typography variant="h3" sx={{ fontVariantNumeric: "tabular-nums" }}>
            {value}
          </Typography>
        )}
        {variance}
        {sub && (
          <Typography variant="caption" color="text.secondary">{sub}</Typography>
        )}
        {action && <Box sx={{ pt: 0.5 }}>{action}</Box>}
      </Stack>
    </Paper>
  );
}

/** A section: an outlined surface with its own heading.
 *
 *  `<Paper variant="outlined" sx={{ p: 3 }}>` wrapping a `SectionHeader` is the
 *  app's section surface — seven uses in AttributionScreen, five in TrustScreen,
 *  more in RetrospectiveScreen and OperatorConsole. Each of those wrote it out.
 *  One agent found the pattern and deliberately did *not* add a file-local
 *  version, on the grounds that a third answer to one question is worse than the
 *  second; this is that answer.
 */
export function Section({
  title, sub, tip, badge, actions, children,
  dense = false, level = "section", surface, className, sx,
}: {
  title: ReactNode;
  sub?: ReactNode;
  tip?: ReactNode;
  badge?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  /** Tighter padding, for a section inside another surface. */
  dense?: boolean;
  /** Which heading rung. Six screens could not use this component when it was
   *  fixed at "section": their panels sit *inside* a section and use the widget
   *  rung, and promoting them would have rendered each panel's name at the same
   *  size as the heading containing it — bigger than its parent in the one
   *  hierarchy §4 describes, and a document-outline entry per panel. */
  level?: "section" | "widget";
  /** The surface to draw. Defaults to `Paper`; pass `ui.Bp` for the four corner
   *  marks the decision screens use. It is a prop rather than an import because
   *  `ui.tsx` imports this file, so the dependency can only run one way — and a
   *  screen where one panel silently lost its corner marks is exactly the
   *  inconsistency this component exists to prevent. */
  surface?: ElementType;
  className?: string;
  sx?: SxProps<Theme>;
}) {
  const Surface = surface ?? Paper;
  return (
    <Surface
      component="section"
      variant="outlined"
      className={className}
      sx={{ p: dense ? 2 : 3, ...sx }}
    >
      <SectionHeader
        level={level} title={title} sub={sub} tip={tip} badge={badge} actions={actions}
      />
      {children}
    </Surface>
  );
}

/** A row of metric tiles that wraps properly.
 *
 *  Six screens wrote `Grid container spacing={2}` + `Grid size={{xs:12,sm:6,md:3}}`
 *  by hand, and the ones that instead used a bare flex `Box` had no minimum
 *  basis at all — at `sm` that put four figures and their captions across 600px.
 *  `per` is how many fit on a wide screen; the small breakpoints are fixed,
 *  because "two up on a tablet, one up on a phone" is not a per-screen decision.
 */
export function TileGrid({
  children, per = 4, spacing = 2,
}: { children: ReactNode; per?: 2 | 3 | 4 | 6; spacing?: number }) {
  const md = (12 / per) as 2 | 3 | 4 | 6;
  return (
    <Grid container spacing={spacing}>
      {Children.map(children, (child, i) =>
        child == null ? null : (
          <Grid key={i} size={{ xs: 12, sm: 6, md }}>{child}</Grid>
        ))}
    </Grid>
  );
}

/** A fact panel: a label and a value, a handful of fixed rows.
 *
 *  This is the one table shape `ui-standards.md` §3 keeps as a real `<table>` —
 *  its row count is set by the design, not by the size of the business, so a
 *  DataGrid here would be a virtualised scroller around four rows.
 *
 *  It exists because `.facttable` styled `td` and nothing else, so every
 *  `<th scope="row">` label fell through to the browser's centred bold. Two
 *  screens worked around that with a local `FACT_CELLS` sx const before the
 *  stylesheet was fixed; a component means the next one inherits the fix.
 */
export function FactTable({
  rows, caption, columns, label, prose = false,
}: {
  /** `[label, value]`, or `[label, value, note]` for a muted third line. With
   *  `columns`, a row is however many cells that header names. */
  rows: ReadonlyArray<ReadonlyArray<ReactNode>>;
  caption?: ReactNode;
  /** Column headings, for a fact panel that is genuinely three columns wide —
   *  "Category / Example / Why it has to go". Without it the first cell is a
   *  row header and the rest are values. */
  columns?: ReadonlyArray<ReactNode>;
  /** The accessible name, where the visible mark sits outside the table — the
   *  panel headings on these screens are qualified by company and catalogue,
   *  and a screen reader listing four tables called "facts" is no listing. */
  label?: string;
  /** Values are sentences, not figures. `.facttable .fv` is a figure treatment
   *  — right-aligned, bold, nowrap, tabular — and two screens needed an escape
   *  from it badly enough to write one each. */
  prose?: boolean;
}) {
  return (
    <Box
      component="table"
      className="facttable"
      aria-label={label}
      /* Top, not the browser's middle: a row whose value runs to three lines
         otherwise centres against a one-line label, and the erasure receipt's
         attestation lists are exactly that shape. */
      sx={{ "& td, & th": { verticalAlign: "top" } }}
    >
      {caption && <Box component="caption" sx={{ captionSide: "top", textAlign: "left", pb: 1 }}>
        <Meta>{caption}</Meta>
      </Box>}
      {columns && (
        <thead>
          <tr>{columns.map((c, i) => <th key={i} scope="col">{c}</th>)}</tr>
        </thead>
      )}
      <tbody>
        {rows.map((cells, i) => (
          <tr key={i}>
            {cells.map((cell, j) =>
              j === 0 ? (
                <Box component="th" scope="row" key={j} sx={{ fontWeight: 400 }}>
                  {cell}
                </Box>
              ) : (
                <td
                  key={j}
                  className={prose ? undefined : "fv"}
                  /* The third cell of a two-column row is the muted note the
                     old signature took as `rows[i][2]`; it is rendered under
                     the label, not as a cell, so the shape stays two-wide. */
                >
                  {cell}
                </td>
              ))}
          </tr>
        ))}
      </tbody>
    </Box>
  );
}

/** The controls above a list: search, segments, filters.
 *
 *  A `Paper`, and one place that decides how they space and wrap. Replaces
 *  `.acct-controls`, `.stock-filters` and `.seg-controls`, which were three
 *  answers to one question. */
export function FilterPanel({
  children, dense = false,
}: { children: ReactNode; dense?: boolean }) {
  return (
    <Paper
      variant="outlined"
      sx={{
        p: dense ? 1 : 1.5, mb: 2,
        display: "flex", flexWrap: "wrap", alignItems: "center",
        gap: 1.5, rowGap: 1.5,
      }}
    >
      {children}
    </Paper>
  );
}

/** A hover-and-focus tooltip for a mark inside a chart.
 *
 * Charts here were relying on the SVG `<title>` element and the HTML `title`
 * attribute. Both are technically tooltips and neither is usable one: the
 * browser decides the delay (around a second), the styling, and the placement,
 * they do not appear on focus so a keyboard never sees them, and on a phone
 * they do not exist at all. A reader hovering a band and getting nothing
 * concludes the chart has no detail rather than that they waited too briefly.
 *
 * MUI's Tooltip is already the house primitive — `StatusChip` uses it — so
 * this is that, with the delays a *chart* wants rather than the ones a form
 * control wants: fast in, because the whole point is sweeping across marks to
 * compare them, and immediate on touch.
 *
 * `Tip` remains the right thing for explaining a *term*; it is a button with a
 * visible "?" affordance, which is correct for prose and wrong for a mark you
 * are already pointing at. This is for the value under the cursor.
 */
export function ChartTip({
  title, children,
}: { title: React.ReactNode; children: React.ReactElement }) {
  return (
    <Tooltip
      title={title}
      arrow
      placement="top"
      enterDelay={60}
      enterNextDelay={30}
      enterTouchDelay={0}
      leaveTouchDelay={4000}
      // Charts live inside `overflow-x: auto` boxes; a portalled popper is not
      // clipped by them, which an absolutely positioned bubble would be.
      slotProps={{ popper: { modifiers: [{ name: "offset", options: { offset: [0, 6] } }] } }}
    >
      {children}
    </Tooltip>
  );
}


/** A decision's human trail: who did what, when, and why.
 *
 * One component because the same block was written twice in `PlatformApp` — the
 * state-derived panel and the signal-derived one — and §2 asks for a shared
 * piece the second time a pattern appears. It was also wrong in both copies in
 * the same way: it rendered the action and the note and dropped the actor,
 * which in a three-person business is the first thing anyone asks.
 *
 * Renders the whole trail, oldest first, so a reversal appears next to what it
 * reversed. Falls back to the single latest action for a decision last touched
 * before the trail existed. Times, not just dates: two actions on one day are
 * the normal case, and a trail whose order you cannot see is not a trail.
 */
export function HumanLog({ action }: { action: HumanAction }) {
  const entries: HumanActionEntry[] =
    action.trail && action.trail.length > 0 ? action.trail : [action];
  return (
    <>
      <SectionHeader title="Human log" level="widget" />
      <Stack spacing={0.5}>
        {entries.map((e, i) => (
          <Stack
            key={`${e.acted_at}-${i}`}
            direction="row"
            spacing={1}
            sx={{ justifyContent: "space-between", alignItems: "baseline" }}
          >
            <Typography variant="body2">
              <strong>{e.action}</strong>
              {" · "}
              {e.note || "no note"}
            </Typography>
            <Typography variant="caption" color="text.secondary" sx={{ whiteSpace: "nowrap" }}>
              {e.actor_name || e.actor_user_id} · {formatDateTime(e.acted_at)}
            </Typography>
          </Stack>
        ))}
      </Stack>
    </>
  );
}
