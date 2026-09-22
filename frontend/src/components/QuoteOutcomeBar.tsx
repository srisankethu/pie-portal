import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { useState, type ReactNode } from "react";
import type { ErpSide, QuoteLossReason, QuoteOutcome, QuoteOutcomeStatus } from "../types";
import { formatDate } from "../when";
import { Tip } from "../Tip";
import { StatusChip, TOUCH, type Tone } from "../platform/kit";
// The five reasons, what each one means downstream, the lifecycle in readable
// words, and the form itself — all from the one place they are written. This
// bar used to carry a form of its own because the shared one could not ask who
// won the business; it can now, so there is one outcome form on this platform
// and this file renders it rather than a second.
import {
  LOSS_REASON_LABELS as REASON_LABELS,
  LOSS_REASON_MEANING as REASON_MEANING,
  RecordOutcomeDialog,
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
 * **The ERP's own word sits beside the person's.** Once the sync has read the
 * document back, `erp` says what the customer did with it there — accepted,
 * declined — and where nobody here has recorded a decision, that word is the
 * outcome of record everywhere else on this platform (Won & lost, the ERP tab,
 * the workspace list). This bar says so in a sentence and offers to record it,
 * because the ERP cannot say *why*, and the reason is what the analysis needs.
 *
 * A `Paper`, not a `Card` (ui-standards §2): this is an action panel about a
 * quote, not a surface wrapping an entity with an identity of its own.
 */

const STATUS_TONE: Record<QuoteOutcomeStatus, Tone> = {
  DRAFT: "neutral", SENT: "info", WON: "good", LOST: "bad",
};

const STATUS_TIP: Record<QuoteOutcomeStatus, string> = {
  DRAFT: "Priced and recorded, not yet sent. Sending it into the customer's books, or marking it as sent, moves it on.",
  SENT: "Written into the customer's books from here, or marked as sent. The books' own status for the document is shown beside it once a sync has read it.",
  WON: "The customer ordered at this price. Terminal — reopening it would rewrite history the margin analysis has already counted.",
  LOST: "Somebody else supplied it, or nobody did. Terminal.",
};

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

/** What the ERP's classification of its own status word reads as here. */
const ERP_WORD: Record<string, string> = { WON: "accepted", LOST: "declined" };

export function QuoteOutcomeBar({ outcome, erp = null, systemLabel = "", onRecord, busy }: {
  outcome: QuoteOutcome | null;
  /** The ERP's own reading of the document this quote became, once synced.
   *  Null until then, and null for a quote marked as sent by hand. */
  erp?: ErpSide | null;
  /** What to call the ERP in the sentence about its word. */
  systemLabel?: string;
  onRecord: (
    status: QuoteOutcomeStatus, lossReason?: QuoteLossReason, lostTo?: string,
    note?: string,
  ) => Promise<void>;
  busy: boolean;
}) {
  const [asking, setAsking] = useState(false);
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
  // ``meaning`` beside the label, from the same module the dialog's own default
  // choices read it from. Replacing this bar's inline loss form with the shared
  // dialog dropped it: the dialog renders the "what this reason means
  // downstream" line only for a choice that carries one, so the Quote Builder
  // lost the sentence that tells somebody apart "they went elsewhere" from
  // "the requirement died" — which are the two halves the competitor mix and
  // the reason mix are built from, and the one thing this vocabulary exists to
  // keep distinct.
  const choices = outcome.loss_reasons.map((code) => ({
    code, label: REASON_LABELS[code], meaning: REASON_MEANING[code],
  }));
  // The ERP has decided and nobody here has: the word everything else already
  // counts, said here with the date, and one press to record it with a reason.
  // With a date, as the server's rule requires: an undated decision is not
  // evidence there, so it is not offered as one here.
  const erpDecided = !decided && erp !== null && erp.decidedOn !== null
    && (erp.outcome === "WON" || erp.outcome === "LOST");

  async function record(
    to: QuoteOutcomeStatus, why?: QuoteLossReason, who?: string, note?: string,
  ) {
    setError(null);
    setSaving(true);
    try {
      await onRecord(to, why, who, note);
      setAsking(false);
    } catch (e) {
      setError((e as Error).message);
      throw e;
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

        {erpDecided && (
          <Box>
            <FieldLabel tip="What the books themselves recorded for this document, as of the last sync. It counts as the outcome everywhere on this platform until somebody here records one — the books cannot say why, and the reason is what the analysis learns from.">
              {systemLabel || "The books"} say
            </FieldLabel>
            <Typography variant="body2">
              {ERP_WORD[erp!.outcome]}
              {erp!.decidedOn ? ` · ${formatDate(erp!.decidedOn)}` : ""}
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
                onClick={() => void record("WON").catch(() => undefined)}
              >
                {erpDecided && erp!.outcome === "WON" ? "Record as won" : "Mark won"}
              </Button>
            )}
            {next.includes("LOST") && (
              <Button
                variant="outlined" size="small" sx={TOUCH} disabled={busy}
                onClick={() => setAsking(true)}
              >
                {erpDecided && erp!.outcome === "LOST" ? "Record as lost…" : "Mark lost…"}
              </Button>
            )}
          </Stack>
        )}
      </Stack>

      {error && !asking && (
        <Alert severity="error" sx={{ mt: 1 }}>{error}</Alert>
      )}

      <RecordOutcomeDialog
        open={asking}
        title="What happened to this quote?"
        summary="Whether somebody else supplied it or the requirement went away are opposite facts about this customer, and only you know which. A loss recorded without one cannot be counted either way later."
        allow={["LOST"]}
        choices={choices}
        onClose={() => setAsking(false)}
        onRecord={(to, why, note, who) => record(to, why, who, note)}
      />
    </Paper>
  );
}
