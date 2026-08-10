import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogContentText from "@mui/material/DialogContentText";
import DialogTitle from "@mui/material/DialogTitle";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useState } from "react";
import type { QuoteLossReason, QuoteOutcome, QuoteOutcomeStatus } from "../types";
import { Tip } from "../Tip";
import { StatusChip, TOUCH, type Tone } from "../platform/kit";

/**
 * What happened to this quote after it went out.
 *
 * The lifecycle DRAFT → SENT → WON/LOST has been modelled, served and typed
 * since the quote-decision audit trail was built, and until now nothing could
 * reach its last two states: DRAFT is set when a quote is snapshotted and SENT
 * when a Zoho estimate is created, both automatically, and no screen ever asked
 * a person whether it was won. So the audit trail could say what was priced and
 * never whether the price worked.
 *
 * **The loss reason is the part that earns this panel.** A lost quote is the
 * one place this platform directly observes a competitor: the customer needed
 * something, we priced it, and somebody else supplied it. That is evidence
 * about what they buy elsewhere — the question `insight/dependency.py` records
 * as otherwise unanswerable. But it only counts if the record says which kind
 * of loss it was, because "another supplier took it" and "the requirement went
 * away" are opposite facts and a bare LOST cannot tell them apart.
 *
 * Which is why the reason is asked for in a dialog rather than offered as an
 * optional field afterwards. The person clicking the button is the one person
 * who knows, and they know it now.
 *
 * A `Paper`, not a `Card` (ui-standards §2): this is an action panel about a
 * quote, not a surface wrapping an entity with an identity of its own.
 */

const REASON_LABELS: Record<QuoteLossReason, string> = {
  PRICE: "Price — somebody quoted lower",
  DELIVERY: "Delivery — somebody could supply and we could not",
  COMPETITOR: "Went to a competitor, for another reason",
  CUSTOMER_CANCELLED: "The requirement went away — nobody supplied it",
  NO_DECISION: "Still undecided, and gone quiet",
};

/** What each answer means for everything downstream, said where it is chosen.
 *  A reason picked to close a dialog is a reason nobody can rely on later. */
const REASON_HELP: Record<QuoteLossReason, string> = {
  PRICE: "Counts as spend that went to a competitor.",
  DELIVERY: "Counts as spend that went to a competitor.",
  COMPETITOR: "Counts as spend that went to a competitor.",
  CUSTOMER_CANCELLED: "Counts as nobody's — no supplier gained this.",
  NO_DECISION: "Counted neither way; it may still move.",
};

const STATUS_TONE: Record<QuoteOutcomeStatus, Tone> = {
  DRAFT: "neutral", SENT: "info", WON: "good", LOST: "bad",
};

const STATUS_TIP: Record<QuoteOutcomeStatus, string> = {
  DRAFT: "Priced and recorded, not yet sent. Creating the Zoho estimate marks it sent.",
  SENT: "With the customer. Recording what happened next is what lets a price be judged against whether it won.",
  WON: "The customer ordered at this price. Terminal — reopening it would rewrite history the margin analysis has already counted.",
  LOST: "Somebody else supplied it, or nobody did. Terminal.",
};

export function QuoteOutcomeBar({ outcome, onRecord, busy }: {
  outcome: QuoteOutcome | null;
  onRecord: (
    status: QuoteOutcomeStatus, lossReason?: QuoteLossReason, lostTo?: string,
  ) => Promise<void>;
  busy: boolean;
}) {
  const [asking, setAsking] = useState(false);
  const [reason, setReason] = useState<QuoteLossReason | "">("");
  const [lostTo, setLostTo] = useState("");
  const [error, setError] = useState<string | null>(null);

  // Nothing to show before a quote has been recorded at all: an outcome control
  // over an empty quote is a question about something that does not exist.
  if (!outcome) return null;

  const status = outcome.status;
  const next = outcome.allowed_next;
  const decided = next.length === 0;
  // Served, never a copy held here — the server's list excludes UNKNOWN, and a
  // second list in this file is one that drifts from the rule enforcing it.
  const choices = outcome.loss_reasons;

  async function record(
    to: QuoteOutcomeStatus, why?: QuoteLossReason, who?: string,
  ) {
    setError(null);
    try {
      await onRecord(to, why, who);
      setAsking(false);
      setReason("");
      setLostTo("");
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <Paper variant="outlined" sx={{ mt: 2, p: 1.5 }}>
      <Stack direction="row" spacing={2} useFlexGap
             sx={{ alignItems: "center", flexWrap: "wrap", rowGap: 1 }}>
        <Box>
          <Typography variant="overline" color="text.secondary" sx={{ display: "block", lineHeight: 1.3 }}>
            Outcome
            <Tip text="Whether this quote won. Recorded here because it is the only way a price can later be judged against what it achieved — and because a lost quote is the one place this platform sees what a customer bought from somebody else." />
          </Typography>
          <StatusChip label={status} tone={STATUS_TONE[status]} tip={STATUS_TIP[status]} />
        </Box>

        {status === "LOST" && (
          <Box>
            <Typography variant="overline" color="text.secondary" sx={{ display: "block", lineHeight: 1.3 }}>
              Why
            </Typography>
            <Typography variant="body2">
              {/* Null is a loss recorded before the reason was asked for. That
                  is not the same as somebody having answered "unknown", and
                  rendering them alike would erase the difference the whole
                  field exists to keep. */}
              {outcome.loss_reason === null
                ? "Not recorded — this quote was decided before the reason was asked for."
                : REASON_LABELS[outcome.loss_reason]}
              {outcome.lost_to ? ` · to ${outcome.lost_to}` : ""}
            </Typography>
          </Box>
        )}

        <Box sx={{ flex: 1 }} />

        {decided ? (
          <Typography variant="body2" color="text.secondary">
            Decided{outcome.decided_at ? ` · ${outcome.decided_at.slice(0, 10)}` : ""}
          </Typography>
        ) : (
          <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: "wrap" }}>
            {next.includes("WON") && (
              <Button
                variant="contained" size="small" sx={TOUCH} disabled={busy}
                onClick={() => record("WON")}
              >
                Mark won
              </Button>
            )}
            {next.includes("LOST") && (
              <Button
                variant="outlined" size="small" sx={TOUCH} disabled={busy}
                onClick={() => setAsking(true)}
              >
                Mark lost…
              </Button>
            )}
          </Stack>
        )}
      </Stack>

      {error && !asking && (
        <Alert severity="error" sx={{ mt: 1 }}>{error}</Alert>
      )}

      <Dialog open={asking} onClose={() => setAsking(false)} fullWidth maxWidth="sm">
        <DialogTitle>What happened to this quote?</DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ mb: 2 }}>
            Whether somebody else supplied it or the requirement went away are
            opposite facts about this customer, and only you know which. A loss
            recorded without one cannot be counted either way later.
          </DialogContentText>
          <TextField
            select fullWidth size="small" autoFocus
            id="quote-loss-reason"
            label="Reason"
            value={reason}
            helperText={reason ? REASON_HELP[reason] : "Required"}
            onChange={(e) => setReason(e.target.value as QuoteLossReason)}
            sx={{ mb: 2 }}
          >
            {choices.map((code) => (
              <MenuItem key={code} value={code}>{REASON_LABELS[code]}</MenuItem>
            ))}
          </TextField>
          <TextField
            fullWidth size="small"
            id="quote-lost-to"
            label="Who won it, if you know"
            placeholder="Supplier name — optional"
            value={lostTo}
            helperText="Left blank is fine. The reason is the part that has to be answered."
            onChange={(e) => setLostTo(e.target.value)}
          />
          {error && <Alert severity="error" sx={{ mt: 2 }}>{error}</Alert>}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setAsking(false)}>Cancel</Button>
          <Button
            variant="contained"
            disabled={!reason || busy}
            onClick={() => reason && record("LOST", reason, lostTo.trim() || undefined)}
          >
            Record the loss
          </Button>
        </DialogActions>
      </Dialog>
    </Paper>
  );
}
