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
      {selectedCount > 0 && (
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span className="text-muted" style={{ fontSize: 12 }}>
            {selectedCount} selected
          </span>
          <button className="btn btn-secondary btn-sm" onClick={() => onDiscount(10)}>
            Apply 10% discount
          </button>
        </div>
      )}
      <button className="btn btn-primary" onClick={onCreateEstimate} disabled={busy || quote.summary.total === 0}>
        Create Zoho estimate
      </button>
    </div>
  );
}
