import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Paper from "@mui/material/Paper";
import Typography from "@mui/material/Typography";
import type { Quote } from "../types";
import { Tip } from "../Tip";
import { CurrencyValue, TOUCH } from "../platform/kit";

/** What the quote comes to, and the one action that sends it.
 *
 * Sticky at the foot of the content column rather than fixed to the viewport.
 * Fixed was right when the Quote Builder owned the whole window; inside the
 * platform shell it slid underneath the navigation drawer and covered whatever
 * was at the bottom of every other screen's scroll.
 */
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
    <Paper
      variant="outlined"
      sx={{
        position: "sticky", bottom: 0, zIndex: 2,
        mt: 3, p: 1.5,
        display: "flex", alignItems: "center", flexWrap: "wrap",
        gap: 3, rowGap: 1.5,
        boxShadow: "var(--shadow-md)",
        bgcolor: "var(--color-neutral-100)",
      }}
    >
      <Stat label="Subtotal" value={quote.summary.subtotal} />
      {quote.summary.taxRate > 0 && (
        /* Label and rate both come from the server. They used to be a literal
           "GST 18%" here beside a number computed from a literal 0.18 in
           store.py — the same fact stated twice, in two languages. */
        <Stat
          label={`${quote.summary.taxLabel} ${(quote.summary.taxRate * 100).toFixed(
            Number.isInteger(quote.summary.taxRate * 100) ? 0 : 1)}%`}
          value={quote.summary.tax}
        />
      )}
      <Stat label="Quotation total" value={quote.summary.grand} strong />

      <Box sx={{ flex: 1 }} />

      {!hasLines && (
        <Typography variant="body2" color="text.secondary">
          Paste an RFQ to start building the quote.
        </Typography>
      )}

      {hasLines && gateBlockedReason && (
        /* An `Alert`, not red text: the severity carries an icon and a role as
           well as a hue, and this sentence is the reason the button beside it
           is disabled. */
        <Alert severity="warning" sx={{ py: 0, maxWidth: 460 }}>
          {gateBlockedReason}
          <Tip text="The block is enforced when the estimate is created, not merely advised — the request goes nowhere until the approval is answered. An approval covers the price it was granted at, so re-pricing a line lower reopens it." />
        </Alert>
      )}

      {selectedCount > 0 && (
        <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
          <Typography variant="body2" color="text.secondary">
            {selectedCount} selected
          </Typography>
          <Button
            variant="outlined"
            size="small"
            sx={TOUCH}
            title="Apply a 10% discount to the selected lines"
            onClick={() => onDiscount(10)}
          >
            Apply 10% discount
          </Button>
        </Box>
      )}

      <Button
        variant="contained"
        sx={TOUCH}
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
    </Paper>
  );
}

/** One figure in the bar. `CurrencyValue` rather than a bare `money()` so the
 *  three of them line up on the decimal, which is the whole reason it exists. */
function Stat({ label, value, strong = false }: {
  label: string; value: number; strong?: boolean;
}) {
  return (
    <Box>
      <Typography variant="overline" color="text.secondary" sx={{ display: "block", lineHeight: 1.3 }}>
        {label}
      </Typography>
      <CurrencyValue
        value={value}
        bold={strong}
        sx={{ fontFamily: "var(--font-heading)", fontSize: 20 }}
      />
    </Box>
  );
}
