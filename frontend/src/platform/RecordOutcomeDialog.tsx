// Asking one person what happened to one quote — the form, in one place.
//
// This was written inside `viz/QuoteOutcomes.tsx`, private to it, and the
// worklist screen beside it needs exactly the same form for exactly the same
// reason: a quote nobody answered has a person who knows, a win is one click,
// and a loss has to say which kind of loss it was before it is worth counting.
// Two copies of that form is two places the rule "a loss must say why" can be
// forgotten, which is the argument `set_outcome` makes for having one writer on
// the server and `intelligence.postOutcome` makes for having one in the
// browser. This is the same argument at the third layer.
//
// **Parameterised over the three things the two screens genuinely differ on**,
// and nothing else:
//
//  - *which id space the quote lives in* — `/insight/quote-outcomes` lists
//    quotes this platform priced and holds a `quote_id` for;
//    `/insight/unrecorded-quotes` lists quotes the ERP raised, which the
//    platform never priced and identifies by `quote_document_ref`. The dialog
//    is told neither: it calls `onRecord`, and the caller picks the writer
//    (`intelligence.outcome` or `intelligence.documentOutcome`). That keeps
//    both id spaces out of a component that has no business telling them apart.
//  - *where the loss vocabulary comes from* — the outcomes screen is served a
//    catalogue by its own endpoint and should keep using it, because a list
//    the server sends cannot drift from the rule the server enforces. The
//    worklist's endpoint serves no catalogue, so it passes
//    `DEFAULT_LOSS_CHOICES` below, which is the client's one copy of the five
//    rather than its second.
//  - *which moves are open* — a quote the ERP has already sent may be marked
//    won or lost; a draft may not be marked won. `allowed_next` decides, and
//    the caller passes it because only the caller was told it.
//
// **The refusal is rendered verbatim, and the dialog stays open holding what
// was typed.** Three of the server's answers here are useless paraphrased: a
// LOST with no reason comes back 422 naming every reason a person may choose,
// an ERP reference answering to two connected books comes back 409 naming both,
// and an outcome already recorded against a different quote comes back 409
// saying so. "Could not record that outcome" throws away the only part that
// tells somebody what to do next. This is the one behavioural change from the
// version that lived in `QuoteOutcomes.tsx`, which put the message in an error
// snackbar and closed nothing — the sentence went past at the bottom of the
// screen while the form it was about sat above it.

import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogContentText from "@mui/material/DialogContentText";
import DialogTitle from "@mui/material/DialogTitle";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import { useEffect, useState, type ReactNode } from "react";

import type { QuoteLossReason, QuoteOutcomeStatus } from "../types";

/** What each reason says, in the words the person choosing it reads.
 *
 *  Keyed by the union rather than by `string`, so the five are exhaustive by
 *  type: adding a sixth `QuoteLossReason` fails to compile here until somebody
 *  writes its label, which is the check a `Record<string, string>` would not
 *  make. Moved out of `components/QuoteOutcomeBar.tsx` — which now imports it
 *  from here — rather than copied, because a second wording of "the requirement
 *  went away" on a second screen is how one fact acquires two meanings. */
export const LOSS_REASON_LABELS: Record<QuoteLossReason, string> = {
  PRICE: "Price — somebody quoted lower",
  DELIVERY: "Delivery — somebody could supply and we could not",
  COMPETITOR: "Went to a competitor, for another reason",
  CUSTOMER_CANCELLED: "The requirement went away — nobody supplied it",
  NO_DECISION: "Still undecided, and gone quiet",
};

/** What each answer means for everything downstream, said where it is chosen.
 *  A reason picked to close a dialog is a reason nobody can rely on later. */
export const LOSS_REASON_MEANING: Record<QuoteLossReason, string> = {
  PRICE: "Counts as spend that went to a competitor.",
  DELIVERY: "Counts as spend that went to a competitor.",
  COMPETITOR: "Counts as spend that went to a competitor.",
  CUSTOMER_CANCELLED: "Counts as nobody's — no supplier gained this.",
  NO_DECISION: "Counted neither way; it may still move.",
};

/** One selectable reason: its code, how it reads, and what choosing it does.
 *
 *  A shape rather than the raw catalogue the outcomes endpoint sends, because
 *  the other caller has no catalogue and mapping one payload into a shape at
 *  one call site is cheaper than teaching this component two payloads. */
export interface LossChoice {
  code: QuoteLossReason;
  label: string;
  meaning?: string;
}

/** The five, from the client's one copy of the wording above.
 *
 *  `NOT_RECORDED` is deliberately absent and cannot be added: it is not a
 *  `QuoteLossReason`, it is the reading state for a loss decided before the
 *  vocabulary existed, and offering it would let somebody file a fresh loss
 *  under "we never asked". */
export const DEFAULT_LOSS_CHOICES: LossChoice[] =
  (Object.keys(LOSS_REASON_LABELS) as QuoteLossReason[]).map((code) => ({
    code,
    label: LOSS_REASON_LABELS[code],
    meaning: LOSS_REASON_MEANING[code],
  }));

const STATUS_WORD: Record<QuoteOutcomeStatus, string> = {
  DRAFT: "Draft", SENT: "Sent", WON: "Won", LOST: "Lost",
};

/** The two moves this dialog can make. `allowed_next` carries the whole
 *  lifecycle, and DRAFT/SENT are things the platform and the ERP set for
 *  themselves — a person is here to say whether it won. */
const DECIDING: QuoteOutcomeStatus[] = ["WON", "LOST"];

export function RecordOutcomeDialog({
  open, title, summary, allow = DECIDING, choices = DEFAULT_LOSS_CHOICES,
  onClose, onRecord,
}: {
  open: boolean;
  /** What this dialog is about, named so the reader can tell it is the row
   *  they clicked. */
  title: ReactNode;
  /** The facts that let somebody answer without opening the quote — who it was
   *  for, what was on it, how long ago. Optional: the caller knows what it has,
   *  and an ERP quote and a platform quote do not carry the same things. */
  summary?: ReactNode;
  /** Which moves the server will accept. Filtered to the deciding pair here,
   *  so a caller may pass `allowed_next` through unedited. */
  allow?: QuoteOutcomeStatus[];
  choices?: LossChoice[];
  onClose: () => void;
  /** Write it. Rejecting is how a refusal reaches this dialog, and the
   *  `Error`'s message is shown as it arrives — see the note at the top. */
  onRecord: (
    status: QuoteOutcomeStatus,
    lossReason: QuoteLossReason | undefined,
    note: string | undefined,
  ) => Promise<void>;
}) {
  const moves = allow.filter((s) => DECIDING.includes(s));
  const first = moves[0] ?? "WON";

  const [status, setStatus] = useState<QuoteOutcomeStatus>(first);
  const [reason, setReason] = useState<QuoteLossReason | "">("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Opening is what clears the form, not closing. A dialog cleared on close
  // wipes the fields while the closing animation is still showing them, and —
  // worse — a save that was refused would lose what the person typed at the
  // moment they were told to change it.
  useEffect(() => {
    if (open) {
      setStatus(first);
      setReason("");
      setNote("");
      setError(null);
    }
    // `first` is derived from `allow`, which is per-row; re-running on it is
    // what makes a row whose only legal move is LOST open on LOST.
  }, [open, first]);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await onRecord(
        status,
        status === "LOST" ? (reason as QuoteLossReason) : undefined,
        note.trim() || undefined,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onClose={busy ? undefined : onClose} fullWidth maxWidth="sm">
      <DialogTitle>{title}</DialogTitle>
      <DialogContent>
        {summary && <DialogContentText sx={{ mb: 2 }}>{summary}</DialogContentText>}
        <Stack spacing={2}>
          <TextField
            select fullWidth label="Outcome" value={status}
            id="record-outcome-status"
            onChange={(e) => setStatus(e.target.value as QuoteOutcomeStatus)}
          >
            {moves.map((s) => (
              <MenuItem key={s} value={s}>{STATUS_WORD[s]}</MenuItem>
            ))}
          </TextField>
          {status === "LOST" && (
            <TextField
              select fullWidth required label="Why we lost it" value={reason}
              id="record-outcome-reason"
              onChange={(e) => setReason(e.target.value as QuoteLossReason)}
              helperText={reason
                ? choices.find((c) => c.code === reason)?.meaning
                : "Required. A loss without a reason can be counted and never "
                  + "learned from: somebody else supplying it and the requirement "
                  + "going away are opposite facts about this customer."}
            >
              {choices.map((c) => (
                <MenuItem key={c.code} value={c.code}>{c.label}</MenuItem>
              ))}
            </TextField>
          )}
          <TextField
            fullWidth multiline minRows={2} label="Note (optional)"
            id="record-outcome-note"
            value={note} onChange={(e) => setNote(e.target.value)}
            helperText="What the list cannot say — the PO number, who took it, what they asked for."
          />
          {/* The server's own sentence, unedited. */}
          {error && <Alert severity="error">{error}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button
          variant="contained" onClick={save}
          disabled={busy || (status === "LOST" && !reason)}
        >
          {busy ? "Recording…" : "Record"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
