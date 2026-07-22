import type { Quote } from "../types";
import { inr } from "../rel";

export function SummaryBar({
  quote,
  selectedCount,
  onDiscount,
  onCreateEstimate,
  busy,
}: {
  quote: Quote;
  selectedCount: number;
  onDiscount: (pct: number) => void;
  onCreateEstimate: () => void;
  busy: boolean;
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
          title={hasLines ? "Create a Zoho estimate from the current quote" : "Add lines before creating the estimate"}
          onClick={onCreateEstimate}
          disabled={busy || !hasLines}
        >
          {busy ? "Creating…" : hasLines ? "Create Zoho estimate" : "Add lines to enable"}
        </button>
      </div>
    </div>
  );
}
