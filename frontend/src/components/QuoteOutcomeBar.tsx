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
import { useState, type ReactNode } from "react";
import type { QuoteLossReason, QuoteOutcome, QuoteOutcomeStatus } from "../types";
import { formatDate } from "../when";
import { Tip } from "../Tip";
import { StatusChip, TOUCH, type Tone } from "../platform/kit";
// The five reasons, what each one means downstream, and the lifecycle in
// readable words — all from the one place they are written. The reasons used to
// live here, and the platform's two outcome screens needed the same wording; a
// second copy of "the requirement went away" is how one fact acquires two
// meanings on two screens. `STATUS_WORD` came the other way: this bar rendered
// the raw enum, so one quote read "LOST" here and "Lost" in the outcomes grid
// and in the dialog that sets it — one vocabulary, spelled two ways.
import {
  LOSS_REASON_LABELS as REASON_LABELS,
  LOSS_REASON_MEANING as REASON_HELP,
  STATUS_WORD,
} from "../platform/RecordOutcomeDialog";

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
 * **Why this is not `platform/RecordOutcomeDialog`, which exists precisely to
 * be the one outcome form.** The two forms ask for different fields because the
 * two writers accept different fields: this one writes `lost_to` — who took the
 * business, which is the competitor evidence above — and the platform's writes
 * a free-text `note`. Routing this through the shared dialog would either drop
 * `lost_to` on the floor or offer a note the Quote Builder's `recordOutcome`
 * cannot carry, and a field that silently goes nowhere is worse than a second
 * form. What *is* shared is everything a copy would let drift: the reason
 * vocabulary, what each reason means downstream, and the words for the states.
 * Unifying the rest means widening `useQuoteIntelligence.recordOutcome` and the
 * shared dialog's callback so one form can carry both fields — and deciding
 * whether the worklist should ask who won, which its writer
 * (`intelligence.documentOutcome`) has no argument for. That is a change to a
 * hook and two contracts, not to a screen.
 *
 * A `Paper`, not a `Card` (ui-standards §2): this is an action panel about a
 * quote, not a surface wrapping an entity with an identity of its own.
 */

const STATUS_TONE: Record<QuoteOutcomeStatus, Tone> = {
  DRAFT: "neutral", SENT: "info", WON: "good", LOST: "bad",
};

const STATUS_TIP: Record<QuoteOutcomeStatus, string> = {
  DRAFT: "Priced and recorded, not yet sent. Sending it into the customer's books marks it sent.",
  SENT: "With the customer. Recording what happened next is what lets a price be judged against whether it won.",
  WON: "The customer ordered at this price. Terminal — reopening it would rewrite history the margin analysis has already counted.",
  LOST: "Somebody else supplied it, or nobody did. Terminal.",
};

/** One dialog on this panel, so one id is enough to name it to a screen
 *  reader. */
const TITLE_ID = "quote-loss-title";

/** The label above one fact in the bar.
 *
 *  The same `variant`, colour and two sx properties were written out twice in
 *  this file, which is the pattern §10 asks to name. Local rather than in
 *  `platform/kit.tsx`: two call sites in one file is not a shared vocabulary,
 *  and a kit entry with one file behind it is the over-abstraction §7 warns
 *  about. It moves there if a third screen wants it. */
function FieldLabel({ children, tip }: { children: ReactNode; tip?: string }) {
  return (
    <Typography
      variant="overline" color="text.secondary"
      sx={{ display: "block", lineHeight: 1.3 }}
    >
      {children}
      {tip ? <Tip text={tip} /> : null}
    </Typography>
  );
}

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
  /** This write, in flight. Distinct from `busy`, which is the Quote Builder's
   *  general "working" flag and is also true while the quote intelligence
   *  reloads — a spinner on the Record button for that would say a save was
   *  happening when none was. `busy` still gates the buttons, as it did. */
  const [saving, setSaving] = useState(false);

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
    setSaving(true);
    try {
      await onRecord(to, why, who);
      setAsking(false);
      setReason("");
      setLostTo("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Paper variant="outlined" sx={{ mt: 2, p: 1.5 }}>
      <Stack direction="row" spacing={2} useFlexGap
             sx={{ alignItems: "center", flexWrap: "wrap", rowGap: 1 }}>
        <Box>
          <FieldLabel tip="Whether this quote won. Recorded here because it is the only way a price can later be judged against what it achieved — and because a lost quote is the one place this platform sees what a customer bought from somebody else.">
            Outcome
          </FieldLabel>
          {/* The word, not the enum. Every grid and dialog that names this
              lifecycle says "Won"; this chip said "WON". */}
          <StatusChip label={STATUS_WORD[status]} tone={STATUS_TONE[status]}
                      tip={STATUS_TIP[status]} />
        </Box>

        {status === "LOST" && (
          <Box>
            <FieldLabel>Why</FieldLabel>
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
            {/* `formatDate`, not `decided_at.slice(0, 10)`. The slice takes the
                UTC day off the timestamp, so a quote decided at half past
                midnight IST was dated the day before — the exact mistake
                `when.ts` was written to end, and it says so in as many
                words. */}
            Decided{outcome.decided_at ? ` · ${formatDate(outcome.decided_at)}` : ""}
          </Typography>
        ) : (
          <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: "wrap" }}>
            {next.includes("WON") && (
              <Button
                variant="contained" size="small" sx={TOUCH} disabled={busy}
                loading={saving} loadingPosition="start"
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

      <Dialog
        open={asking} onClose={() => setAsking(false)} fullWidth maxWidth="sm"
        // MUI does not wire the title to the dialog on its own, so without this
        // a screen reader announces "dialog" and nothing else.
        aria-labelledby={TITLE_ID}
      >
        <DialogTitle id={TITLE_ID}>What happened to this quote?</DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ mb: 2 }}>
            Whether somebody else supplied it or the requirement went away are
            opposite facts about this customer, and only you know which. A loss
            recorded without one cannot be counted either way later.
          </DialogContentText>
          {/* Full-size fields, matching `RecordOutcomeDialog`. §8 asks for one
              spacing across forms and these were `size="small"` here and
              default there, so the same question was asked at two sizes on two
              screens. */}
          <TextField
            select fullWidth autoFocus
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
            fullWidth
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
          {/* Cancel stays live throughout, deliberately: `busy` is the Quote
              Builder's, and a dialog that cannot be dismissed because something
              else on the screen is loading is a trap. */}
          <Button onClick={() => setAsking(false)}>Cancel</Button>
          {/* MUI's own spinner while the write is in flight — §7. Without it the
              only feedback on a slow save was a button that had stopped
              responding. The disabled condition is unchanged: `loading` disables
              too, so this is still "no reason, or the screen is busy". */}
          <Button
            variant="contained"
            loading={saving} loadingPosition="start"
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
