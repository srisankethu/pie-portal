// The four states every view has, in one place.
//
// Loading, empty, error and loaded are not decoration — they are most of what
// separates a product from a dashboard. A dashboard shows a spinner and then a
// blank rectangle; a product tells you *why* it is blank and what would change
// it. So `empty` takes the server's own `empty_reason`, which is written where
// the query happened and therefore knows the difference between "nothing synced
// yet", "nothing qualified against your threshold" and "you cannot see this".
//
// Accessibility is built in rather than bolted on: the panel is a labelled
// region, loading is announced politely, and errors are assertive.

import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import { LoadingState } from "../kit";
import type { ReactNode } from "react";
import type { ScaleLinear } from "d3-scale";

export type ViewState = "loading" | "error" | "empty" | "ready";

export function stateOf(
  loading: boolean,
  error: string | null,
  emptyReason: string | null | undefined,
): ViewState {
  if (loading) return "loading";
  if (error) return "error";
  if (emptyReason) return "empty";
  return "ready";
}

export function Panel({
  title,
  question,
  state,
  error,
  emptyReason,
  onRetry,
  actions,
  children,
  wide,
}: {
  title: string;
  /** The question this panel answers, shown under the title. Every view in this
   *  product answers one — writing it down keeps panels from accumulating that
   *  answer none. */
  question?: string;
  state: ViewState;
  error?: string | null;
  emptyReason?: string | null;
  onRetry?: () => void;
  actions?: ReactNode;
  children: ReactNode;
  wide?: boolean;
}) {
  const labelId = `panel-${title.replace(/\W+/g, "-").toLowerCase()}`;
  return (
    <section
      className={`viz-panel${wide ? " viz-panel-wide" : ""}`}
      aria-labelledby={labelId}
    >
      <header className="viz-panel-head">
        <div>
          <h3 id={labelId}>{title}</h3>
          {question && <p className="viz-question">{question}</p>}
        </div>
        {actions && <div className="viz-panel-actions">{actions}</div>}
      </header>

      {/* A skeleton, not a spinner: it reserves the height the chart will take,
          so the page does not jump when data lands. MUI's own rather than the
          bar-shaped shimmer that used to live in viz.css — that one was
          correct, down to its reduced-motion rule, but it was a second answer
          to a question `LoadingState` already answers everywhere else. */}
      {/* Not `.viz-state`: that grid is `justify-items: start`, which is right
          for a sentence and wrong for a skeleton — it would shrink to its
          content and stop reserving the chart's width. */}
      {state === "loading" && (
        <Box sx={{ py: 2.25 }}>
          <LoadingState rows={1} height={120} label={`Loading ${title.toLowerCase()}…`} />
        </Box>
      )}

      {state === "error" && (
        <div className="viz-state viz-state-error" role="alert">
          <p className="viz-state-title">This did not load.</p>
          <p className="viz-muted">{error}</p>
          {onRetry && (
            <Button type="button" variant="outlined" size="small" onClick={onRetry}>
              Try again
            </Button>
          )}
        </div>
      )}

      {state === "empty" && (
        <div className="viz-state">
          <p className="viz-state-title">Nothing to show yet</p>
          {/* The server's own words. It knows which kind of empty this is. */}
          <p className="viz-muted">{emptyReason}</p>
        </div>
      )}

      {state === "ready" && children}
    </section>
  );
}

/** A figure with its own accessible description and a table fallback.
 *
 *  The table is not a courtesy — a chart that cannot be read by a screen reader
 *  or copied into a mail is a chart that gets screenshotted and retyped. */
export function Figure({
  caption,
  summary,
  table,
  children,
}: {
  caption: string;
  summary: string;
  table?: ReactNode;
  children: ReactNode;
}) {
  return (
    <figure className="viz-figure">
      <div role="img" aria-label={summary}>
        {children}
      </div>
      <figcaption>{caption}</figcaption>
      {table && (
        <details className="viz-table-fallback">
          <summary>View as a table</summary>
          {table}
        </details>
      )}
    </figure>
  );
}

/** A vertical scale you can actually read a number off: round gridlines across
 *  the plot, labelled down the left edge.
 *
 * Neither SVG chart here had one. The waterfall drew a single baseline, so a
 * bar's height was comparable to its neighbours and to nothing else — you could
 * see that revenue fell without being able to say roughly how far. The
 * landscape labelled its two *padded domain endpoints*, numbers like 10% and
 * 23% that nobody chose and that move whenever the data does.
 *
 * `scale.ticks()` is the reason `d3-scale` is a dependency at all. Picking tick
 * values that are round, evenly spaced and inside the domain is a real
 * algorithm, and the one everybody writes instead — `min + i * (max - min) / n`
 * — yields 10.4%, 13.6%, 16.8%: worse than no axis, because it looks
 * deliberate. Everything else d3 offers, this codebase either does not need or
 * already does correctly by hand.
 *
 * Ticks are advisory: `count` is a target, and d3 returns the nearest round
 * number of them. That is the right trade — a round value at an odd spacing
 * reads better than an odd value at a round spacing.
 */
export function ValueAxis({
  scale, x0, x1, format, count = 4,
}: {
  scale: ScaleLinear<number, number>;
  /** The gridline spans the plot; the label sits just outside `x0`. */
  x0: number;
  x1: number;
  format: (v: number) => string;
  count?: number;
}) {
  return (
    // Decoration for a screen reader: `Figure` already carries the summary, and
    // reading eight tick values aloud before the data is noise.
    <g aria-hidden="true">
      {scale.ticks(count).map((t) => {
        const y = scale(t);
        return (
          <g key={t}>
            <line x1={x0} x2={x1} y1={y} y2={y} className="viz-gridline" />
            <text x={x0 - 6} y={y + 4} textAnchor="end" className="viz-axis">
              {format(t)}
            </text>
          </g>
        );
      })}
    </g>
  );
}
