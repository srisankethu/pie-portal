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

import type { ReactNode } from "react";

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

      {state === "loading" && (
        <div className="viz-state" role="status" aria-live="polite">
          {/* A shaped skeleton, not a spinner: it reserves the height the chart
              will take, so the page does not jump when data lands. */}
          <div className="viz-skeleton" aria-hidden="true">
            <span /><span /><span /><span /><span />
          </div>
          <p className="viz-muted">Loading {title.toLowerCase()}…</p>
        </div>
      )}

      {state === "error" && (
        <div className="viz-state viz-state-error" role="alert">
          <p className="viz-state-title">This did not load.</p>
          <p className="viz-muted">{error}</p>
          {onRetry && (
            <button type="button" className="btn btn-secondary btn-sm" onClick={onRetry}>
              Try again
            </button>
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
