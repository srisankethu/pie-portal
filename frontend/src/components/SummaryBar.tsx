import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Paper from "@mui/material/Paper";
import Typography from "@mui/material/Typography";
import type { Quote } from "../types";
import type { Blocker } from "./lineProblems";
import { Tip } from "../Tip";
import { CurrencyValue, TOUCH } from "../platform/kit";

/** What the quote comes to, and the one action that sends it.
 *
 * Sticky at the foot of the content column rather than fixed to the viewport.
 * Fixed was right when the Quote Builder owned the whole window; inside the
 * platform shell it slid underneath the navigation drawer and covered whatever
 * was at the bottom of every other screen's scroll.
 *
 * **And not sticky at all on a phone.** Sticky is cheap while this is one row:
 * at a desk it is about 80px and the grid keeps the rest. At 390px the same
 * content wraps into five stacked blocks — two figures, what the total covers,
 * the alert naming what is unsettled, and the send — measured at 254px of an
 * 844px screen, and it sat on top of the line cards for the whole of the work
 * it was summarising. A running total is worth a third of a desk; it is not
 * worth a third of a phone, where the thing underneath it is the rate field
 * somebody is there to fill in. Below `sm` it is the last block on the page,
 * which is where you scroll to when you are ready to send.
 */
export function SummaryBar({
  quote,
  selectedCount,
  onDiscount,
  onCreateEstimate,
  busy,
  readOnly = false,
  gateBlockedReason,
  blockers,
  covers }: {
  quote: Quote;
  /** The reader may not change this quote: the discount and the send are
   *  disabled, and the send says whose quote it is. */
  readOnly?: boolean;
  selectedCount: number;
  onDiscount: (pct: number) => void;
  onCreateEstimate: () => void;
  busy: boolean;
  /** Why the quote cannot be sent, from the approval gate. Shown here so the
   *  reason sits next to the button rather than arriving as a failure. */
  gateBlockedReason: string | null;
  /** Everything still standing between this quote and the customer, counted
   *  the way the grid shows it — see `lineProblems.blockersFor`.
   *
   *  The send used to be a button that looked ready until it was pressed, and
   *  then a sentence about work done fourteen lines ago. It says how many
   *  things remain before anybody presses it, and every one of them is marked
   *  on the row it belongs to. */
  blockers: Blocker[];
  /** What the total covers, and what it leaves out. */
  covers: string | null;
}) {
  const hasLines = quote.lines.length > 0;
  const sent = quote.estimate !== null && quote.estimate !== undefined;
  // What the send actually creates, in the words of the system it creates it
  // in — "Zoho Books estimate", "Dynamics 365 Business Central sales quote".
  // This button said "Create Zoho estimate" to every customer, which names a
  // record type most of them do not have. See `Quote.systemLabel`.
  //
  // The bare document term where no system is connected: `systemLabel` is
  // "your books" there, and "Create your books quote" is not a sentence.
  const document = quote.system
    ? `${quote.systemLabel} ${quote.documentTerm}`
    : quote.documentTerm;

  return (
    <Paper
      variant="outlined"
      sx={{
        position: { xs: "static", sm: "sticky" }, bottom: 0, zIndex: 2,
        mt: 3, p: 1.5,
        display: "flex", alignItems: "center", flexWrap: "wrap",
        gap: 3, rowGap: 1.5,
        boxShadow: "var(--shadow-md)",
        bgcolor: "var(--color-neutral-100)",
      }}
    >
      <Stat
        label="Subtotal"
        value={quote.summary.subtotal}
        /* What the figure leaves out, next to the figure. A four-line quote
           with two unresolved lines printed a Quotation total in the same
           weight as a finished one, with nothing saying it was the total of
           half a quote — and every priced line was still at the catalogue rate
           nobody had looked at. */
        note={caveat(quote)}
      />
      {quote.summary.tax > 0 && (
        /* Label and rate both come from the server. They used to be a literal
           "GST 18%" here beside a number computed from a literal 0.18 in
           store.py — the same fact stated twice, in two languages.

           Shown on the *amount*, not on the rate. The rate is null whenever the
           priced lines do not share one, and keying the whole Stat off the rate
           meant a mixed-rate quote dropped the tax line off the screen while
           still carrying it in the Quotation total below — money in the total
           with nothing on screen accounting for it. */
        <Stat
          label={taxLabel(quote.summary)}
          value={quote.summary.tax}
          note={taxNote(quote.summary)}
        />
      )}
      <Stat label="Quotation total" value={quote.summary.grand} strong />

      <Box sx={{ flex: 1 }} />

      {!hasLines && (
        <Typography variant="body2" color="text.secondary">
          Paste an RFQ to start building the quote.
        </Typography>
      )}

      {/* What the total is the total of. Beside the figures rather than under
          the subtotal, because it is a sentence about the quote and not about
          one of the three numbers. */}
      {hasLines && covers && (
        <Typography variant="body2" color="text.secondary" sx={{ maxWidth: "44ch" }}>
          {covers}
        </Typography>
      )}

      {hasLines && blockers.length > 0 && (
        /* Every one of these is marked on its own row. The list here is the
           count and the map, not the explanation — the explanation is next to
           the line it is about, which is the whole of this screen's argument
           with the version that reported them all at Send. */
        <Alert severity="warning" sx={{ py: 0, maxWidth: 460 }}>
          <b>{blockers.length === 1 ? "One thing" : `${blockers.length} things`} to settle first</b>
          {" — "}
          {blockers.map((b) => b.text).join(" · ")}
          {gateBlockedReason && (
            <Tip text="The block is enforced when the estimate is created, not merely advised — the request goes nowhere until the approval is answered. An approval covers the price it was granted at, so re-pricing a line lower reopens it." />
          )}
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
            disabled={readOnly}
          >
            Apply 10% discount
          </Button>
        </Box>
      )}

      {/* What this quote has already sent. The estimate number used to exist
          only inside a three-second snackbar, so the single most important
          outcome of the whole screen was gone before it could be written down —
          and the button beside it still said "Create Zoho estimate" in primary,
          which is how the same quote reached Zoho three times. */}
      {sent && (
        <Chip
          size="small"
          color={quote.estimate!.current ? "success" : "default"}
          variant="outlined"
          label={quote.estimate!.current
            ? `Sent · ${quote.estimate!.systemLabel} · ${quote.estimate!.number}`
            : `Sent · ${quote.estimate!.systemLabel} · ${quote.estimate!.number} · amended since`}
        />
      )}

      <Button
        variant={sent && quote.estimate!.current ? "outlined" : "contained"}
        sx={TOUCH}
        title={
          readOnly
            ? `Only ${quote.owner?.name || "the owner"} can send this quote.`
            : blockers.length
              ? `${blockers.map((b) => b.text).join(" · ")} — each is marked on its own line above.`
              : (!hasLines
            ? "Add lines before creating the estimate"
            : sent && quote.estimate!.current
              ? `This quote is already ${document} ${quote.estimate!.number}. `
                + "Nothing has changed since, so sending again returns the same one."
              : sent
                ? `The quote has changed since it was sent — this creates a new ${quote.documentTerm}`
                : `Send this quote into ${quote.systemLabel}`)
        }
        onClick={onCreateEstimate}
        disabled={busy || readOnly || !hasLines || blockers.length > 0
                  || (sent && quote.estimate!.current)}
      >
        {/* "Send", not "Create".
          *
          * This button said `Create ${document}` — "Create Zoho Books estimate"
          * — about 700px below a "New quote" button that genuinely creates a
          * quote. Two near-homographs on one screen for opposite actions, and
          * the destructive one wore the gentler verb: this writes a document
          * into the customer's books and cannot be taken back from here. The
          * tooltip beside it has said "Send this quote into Zoho Books" the
          * whole time, so the label was the half that was wrong.
          *
          * One verb through the whole flow, including the amended case, and
          * the disabled states keep the same name rather than renaming the
          * control after its own precondition — "Add lines to enable" told a
          * reader what was missing at the cost of the button's identity, and
          * the reason is on the tooltip either way. */}
        {busy
          ? "Sending…"
          : blockers.length
            ? `${blockers.length} to settle first`
            : sent && quote.estimate!.current
              ? "Already sent"
              : sent
                ? `Send amendment to ${quote.systemLabel}`
                : `Send to ${quote.systemLabel}`}
      </Button>
    </Paper>
  );
}

/** One figure in the bar. `CurrencyValue` rather than a bare `money()` so the
 *  three of them line up on the decimal, which is the whole reason it exists. */
function Stat({ label, value, strong = false, note }: {
  label: string; value: number; strong?: boolean;
  /** What this figure does not yet account for. */
  note?: string | null;
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
      {note && (
        <Typography variant="caption" color="text.secondary" sx={{ display: "block", lineHeight: 1.3 }}>
          {note}
        </Typography>
      )}
    </Box>
  );
}

/** What the subtotal is not counting, or has not been looked at.
 *
 *  Both facts or neither: a line with no rate is missing from the figure, and a
 *  line still at the catalogue rate is in it at a number nobody chose. Stated
 *  once, under the figure it qualifies, rather than as a fourth banner. */
function caveat(quote: Quote): string | null {
  const { unpriced, atListPrice } = quote.summary;
  const parts: string[] = [];
  if (unpriced > 0) parts.push(`${unpriced} line(s) not priced`);
  if (atListPrice > 0) parts.push(`${atListPrice} still at list`);
  return parts.length ? parts.join(" · ") : null;
}

/** The tax heading: the rate where the priced lines share one, and the tax's
 *  name alone where they do not.
 *
 *  "GST" over a quote holding an 18% line and a 12% one is the honest heading.
 *  "GST 18%" over the same quote is a statement about a document the customer
 *  will receive, and it is false. */
function taxLabel(s: Quote["summary"]): string {
  if (s.taxRate === null) return `${s.taxLabel} (mixed rates)`;
  const pct = s.taxRate * 100;
  return `${s.taxLabel} ${pct.toFixed(Number.isInteger(pct) ? 0 : 1)}%`;
}

/** What the tax figure rests on, in the same place `caveat` says what the
 *  subtotal leaves out — and for the same reason. A total assembled from rates
 *  the books stated and one assembled from a configured default read
 *  identically until somebody says which this is. */
function taxNote(s: Quote["summary"]): string | null {
  const { assumed, defaultRate } = s.taxBasis;
  if (assumed === 0) return null;
  const pct = defaultRate * 100;
  const rate = pct.toFixed(Number.isInteger(pct) ? 0 : 1);
  return `${rate}% assumed on ${assumed} line(s) — no rate in the books`;
}
