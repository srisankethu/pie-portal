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

import type { ReactNode } from "react";
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
import Link from "@mui/material/Link";
import Paper from "@mui/material/Paper";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import ArrowUpward from "@mui/icons-material/ArrowUpward";
import ArrowDownward from "@mui/icons-material/ArrowDownward";
import RemoveIcon from "@mui/icons-material/Remove";

import { money } from "../money";
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
  title, sub, tip, actions, level = "page",
}: {
  title: ReactNode;
  /** What this screen or section answers. Every view in this product answers
   *  one — writing it down keeps sections from accumulating that answer none. */
  sub?: ReactNode;
  tip?: string;
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
        <Typography variant={variant} sx={{ mb: sub ? 0.5 : 0 }}>
          {title}
          {tip ? <Tip text={tip} /> : null}
        </Typography>
        {sub && (
          <Typography variant="body2" color="text.secondary" sx={{ maxWidth: "68ch" }}>
            {sub}
          </Typography>
        )}
      </Box>
      {actions && (
        <Stack direction="row" spacing={1} useFlexGap sx={{ alignItems: "center" }}>
          {actions}
        </Stack>
      )}
    </Stack>
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
  value: number | null | undefined;
  /** Show an explicit + for a positive. For a movement, not for a balance. */
  signed?: boolean;
  bold?: boolean;
  sx?: object;
}) {
  if (value == null) return <Box component="span" sx={sx}>—</Box>;
  const sign = signed ? (value >= 0 ? "+" : "−") : value < 0 ? "−" : "";
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
      {sign}{money(Math.abs(value))}
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
      sx={{ ...TOUCH, borderRadius: 999, px: 0.5 }}
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
  title = "Nothing to show yet", reason, action,
}: { title?: ReactNode; reason?: ReactNode; action?: ReactNode }) {
  return (
    <Paper variant="outlined" sx={{ p: 4, textAlign: "center" }}>
      <Typography variant="subtitle1" sx={{ mb: reason ? 0.5 : 0 }}>{title}</Typography>
      {reason && (
        <Typography variant="body2" color="text.secondary" sx={{ maxWidth: "60ch", mx: "auto" }}>
          {reason}
        </Typography>
      )}
      {action && <Box sx={{ mt: 2 }}>{action}</Box>}
    </Paper>
  );
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
  title = "This did not load", error, onRetry, busy = false,
}: { title?: ReactNode; error?: ReactNode; onRetry?: () => void; busy?: boolean }) {
  return (
    <Alert
      severity="error"
      action={onRetry ? (
        <Button color="inherit" size="small" onClick={onRetry} disabled={busy}>
          {busy ? "Retrying…" : "Try again"}
        </Button>
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
  label, value, sub, variance, tip, action,
}: {
  label: ReactNode;
  value: ReactNode;
  sub?: ReactNode;
  variance?: ReactNode;
  tip?: string;
  action?: ReactNode;
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
        <Typography variant="h3" sx={{ fontVariantNumeric: "tabular-nums" }}>
          {value}
        </Typography>
        {variance}
        {sub && (
          <Typography variant="caption" color="text.secondary">{sub}</Typography>
        )}
        {action && <Box sx={{ pt: 0.5 }}>{action}</Box>}
      </Stack>
    </Paper>
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
