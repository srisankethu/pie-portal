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
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
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
 *  as links: correct semantics for something that navigates within a SPA, but
 *  every one of them had to re-declare its own underline, colour and focus
 *  ring, and the disabled state was a second class (`.multiple-name:disabled`)
 *  that only one of the five had. `Link component="button"` keeps the button
 *  semantics and brings the rest from the theme. */
export function InlineLink({
  children, onClick, disabled = false, bold = false, sx,
}: {
  children: ReactNode;
  onClick: () => void;
  disabled?: boolean;
  /** For a name that heads its own row rather than sitting mid-sentence. */
  bold?: boolean;
  sx?: object;
}) {
  return (
    <Link
      component="button"
      type="button"
      variant="body2"
      underline={disabled ? "none" : "always"}
      disabled={disabled}
      onClick={onClick}
      sx={{
        font: "inherit",
        textAlign: "left",
        minWidth: 0,
        // A `<button>` is `vertical-align: middle` by default, which lifts the
        // name off the baseline of the amount sitting next to it.
        verticalAlign: "baseline",
        fontWeight: bold ? 600 : undefined,
        // A disabled name is still worth reading — it is a customer who cannot
        // be opened, not an absent one.
        color: disabled ? "text.secondary" : undefined,
        cursor: disabled ? "default" : "pointer",
        ...sx,
      }}
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
