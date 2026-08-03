import type { Quote } from "../types";
import { inr } from "../rel";
import { Tip } from "../Tip";

export function SummaryBar({
  quote,
  selectedCount,
  onDiscount,
  onCreateEstimate,
  busy,
  gateBlockedReason,
}: {
  quote: Quote;
  selectedCount: number;
  onDiscount: (pct: number) => void;
  onCreateEstimate: () => void;
  busy: boolean;
  /** Why the quote cannot be sent, from the approval gate. Shown here so the
   *  reason sits next to the button rather than arriving as a failure. */
  gateBlockedReason: string | null;
}) {
  const hasLines = quote.lines.length > 0;

  return (
    <div className="summary">
      <div className="stat">
        <div className="label">Subtotal</div>
        <div className="value">{inr(quote.summary.subtotal)}</div>
      </div>
      <div className="stat">
        <div className="label">GST 18%</div>
        <div className="value">{inr(quote.summary.gst)}</div>
      </div>
      <div className="stat">
        <div className="label">Quotation total</div>
        <div className="value">{inr(quote.summary.grand)}</div>
      </div>
      <div className="spacer" />
      <div className="summary-actions">
        {!hasLines && <span className="summary-help">Paste an RFQ to start building the quote.</span>}
        {hasLines && gateBlockedReason && (
          <span className="summary-blocked">
            {gateBlockedReason}
            <Tip text="The block is enforced when the estimate is created, not merely advised — the request goes nowhere until the approval is answered. An approval covers the price it was granted at, so re-pricing a line lower reopens it." />
          </span>
        )}
        {selectedCount > 0 && (
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span className="text-muted" style={{ fontSize: 12 }}>
              {selectedCount} selected
            </span>
            <button className="btn btn-secondary btn-sm" title="Apply a 10% discount to the selected lines" onClick={() => onDiscount(10)}>
              Apply 10% discount
            </button>
          </div>
        )}
        <button
          className="btn btn-primary"
          title={
            gateBlockedReason ?? (hasLines
              ? "Create a Zoho estimate from the current quote"
              : "Add lines before creating the estimate")
          }
          onClick={onCreateEstimate}
          disabled={busy || !hasLines || !!gateBlockedReason}
        >
          {busy
            ? "Creating…"
            : gateBlockedReason
              ? "Awaiting approval"
              : hasLines
                ? "Create Zoho estimate"
                : "Add lines to enable"}
        </button>
      </div>
    </div>
  );
}
