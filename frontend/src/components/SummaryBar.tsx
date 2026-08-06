import Button from "@mui/material/Button";
import type { Quote } from "../types";
import { Tip } from "../Tip";
import { money } from "../money";

export function SummaryBar({
  quote,
  selectedCount,
  onDiscount,
  onCreateEstimate,
  busy,
  gateBlockedReason }: {
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
        <div className="value">{money(quote.summary.subtotal)}</div>
      </div>
      {quote.summary.taxRate > 0 && (
        <div className="stat">
          {/* Label and rate both come from the server. They used to be a literal
              "GST 18%" here beside a number computed from a literal 0.18 in
              store.py — the same fact stated twice, in two languages. */}
          <div className="label">
            {quote.summary.taxLabel} {(quote.summary.taxRate * 100).toFixed(
              Number.isInteger(quote.summary.taxRate * 100) ? 0 : 1)}%
          </div>
          <div className="value">{money(quote.summary.tax)}</div>
        </div>
      )}
      <div className="stat">
        <div className="label">Quotation total</div>
        <div className="value">{money(quote.summary.grand)}</div>
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
            <Button variant="outlined" size="small" title="Apply a 10% discount to the selected lines" onClick={() => onDiscount(10)}>
              Apply 10% discount
            </Button>
          </div>
        )}
        <Button
          variant="contained"
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
        </Button>
      </div>
    </div>
  );
}
