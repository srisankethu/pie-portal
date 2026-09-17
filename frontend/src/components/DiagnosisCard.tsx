import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useState } from "react";
import {
  FactTable, FieldLabel, FormDialog, Meta, PanelMark, StatusChip, TOUCH,
} from "../platform/kit";
import type { Tone } from "../platform/kit";

/**
 * One quote line's commercial diagnosis, as a salesperson sees it.
 *
 * **Every string on this card came from the server already written.** The
 * headline, the reason, the note and the qualification are assembled by
 * `commercial/quote_diagnosis/render.py` from values the deterministic engine
 * computed; nothing here composes a sentence, formats a figure, or decides what
 * a code means. That is not tidiness — a rule worded one way on the server and
 * another way in the browser is two rules, and the one people read is the one
 * that was never reviewed.
 *
 * **There is nothing to withhold here.** The payload behind this component is
 * built from a record type with no cost, margin, opportunity or peer field on
 * it, so the component has no `{mgmt && …}` guard and needs none. `MFLOOR` was
 * a guard that was right with one line below it that was not; this card cannot
 * have that shape because the data never arrives.
 *
 * **Silent by default.** `renders` is the server's answer to "is this worth
 * interrupting somebody for", and the component returns nothing when it is
 * false. A caller that drew the card anyway would be overriding a threshold
 * decision made against a versioned policy, from a place that has no idea what
 * the policy is.
 */

export type DiagnosisView = {
  quote_diagnosis_id: string | null;
  line_id: string;
  renders: boolean;
  /** Whether the engine had comparable evidence for this line at all.
   *
   *  Not the same as `renders`, and the gap between them is what a reader of a
   *  finished document needs: a line that does not render may have been judged
   *  and found ordinary, or may never have been comparable to anything. Only
   *  the first is good news. */
  comparable: boolean;
  headline: string;
  quoted: string;
  historical: string;
  evidence: string;
  evidence_detail: string;
  why: string;
  note: string;
  qualification: string;
  actions: string[];
};

export type DismissReason = { code: string; label: string };

/** Evidence strength, as a `Chip` rather than coloured text — `ui-standards` §6.
 *
 *  Strong is not "good" and weak is not "bad": they say how much the band is
 *  worth believing, so the tone ramps with confidence and stops short of
 *  success/error, which would read as a verdict on the price. */
const STRENGTH_TONE: Record<string, Tone> = {
  Strong: "good",
  Moderate: "info",
  Weak: "warn",
};

export function DiagnosisCard({
  diagnosis, reasons, onReviewPrice, onDismiss, dismissing = false,
}: {
  diagnosis: DiagnosisView;
  /** Served by `GET /api/v1/quote-diagnosis/reasons`, never hardcoded here: a
   *  front end offering a reason the service refuses is a dead button somebody
   *  discovers in front of a customer. */
  reasons: DismissReason[];
  onReviewPrice?: (lineId: string) => void;
  onDismiss?: (diagnosisId: string, reasonCode: string, note: string) => void;
  dismissing?: boolean;
}) {
  const [open, setOpen] = useState(false);

  if (!diagnosis.renders) return null;

  const canDismiss = Boolean(diagnosis.quote_diagnosis_id && onDismiss);

  return (
    <Paper variant="outlined" sx={{ p: 2.5 }}>
      <Stack spacing={2}>
        <Box>
          <PanelMark mark="ink">Quote diagnosis</PanelMark>
          <Typography variant="subtitle1" sx={{ mt: 0.5, fontWeight: 600 }}>
            {diagnosis.headline}
          </Typography>
        </Box>

        {/* A label and a value, two rows — the case `ui-standards` §3 keeps a
            `<table>` for. Its row count is fixed by the card, not by the size
            of the business, so a DataGrid here would be a grid for two rows. */}
        <FactTable
          label={`Price comparison for line ${diagnosis.line_id}`}
          rows={[
            ["Quoted", diagnosis.quoted],
            ["Historical", diagnosis.historical],
          ]}
        />

        <Box>
          <FieldLabel>Evidence</FieldLabel>
          <Stack direction="row" spacing={1} sx={{ mt: 0.5, alignItems: "center" }}>
            <StatusChip
              label={diagnosis.evidence}
              tone={STRENGTH_TONE[diagnosis.evidence] ?? "neutral"}
            />
            <Meta>{diagnosis.evidence_detail}</Meta>
          </Stack>
        </Box>

        <Box>
          <FieldLabel>Why?</FieldLabel>
          <Typography variant="body2" sx={{ mt: 0.5 }}>{diagnosis.why}</Typography>
          {diagnosis.note && (
            <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
              {diagnosis.note}
            </Typography>
          )}
        </Box>

        {/* Printed on every card without exception. A reader who is not told
            that history can hold unrecorded exceptional pricing reads a band as
            a rule, and these books genuinely hold deals done outside them. */}
        <Meta>{diagnosis.qualification}</Meta>

        <Stack
          direction={{ xs: "column", sm: "row" }}
          spacing={1}
          /* Stacked full-width on a phone: this desk quotes from a machine
             shop, and two buttons sharing a 390px row are two 44px targets a
             thumb has to choose between. */
          sx={{ "& .MuiButton-root": { ...TOUCH } }}
        >
          {diagnosis.actions.includes("REVIEW_PRICE") && (
            <Button
              variant="contained"
              onClick={() => onReviewPrice?.(diagnosis.line_id)}
              disabled={!onReviewPrice}
            >
              Review price
            </Button>
          )}
          {diagnosis.actions.includes("DISMISS") && (
            <Button
              variant="outlined"
              onClick={() => setOpen(true)}
              disabled={!canDismiss}
            >
              Dismiss — reason?
            </Button>
          )}
        </Stack>
      </Stack>

      <DismissDialog
        open={open}
        reasons={reasons}
        busy={dismissing}
        onClose={() => setOpen(false)}
        onConfirm={(code, note) => {
          if (diagnosis.quote_diagnosis_id) {
            onDismiss?.(diagnosis.quote_diagnosis_id, code, note);
          }
          setOpen(false);
        }}
      />
    </Paper>
  );
}

/**
 * Why this card was wrong, in a vocabulary somebody can count.
 *
 * `FormDialog` rather than `Dialog`: this is a form somebody fills in, so it is
 * full screen below `sm` — `ui-standards` §12. A centred sheet at 390px is a
 * letterbox with its own scrollbar inside the page's.
 *
 * The reason is required and the note is not, which is the whole design. A
 * dismissal is the cheapest labelled data this engine will ever get, and free
 * text nobody can aggregate tunes nothing.
 */
function DismissDialog({
  open, reasons, busy, onClose, onConfirm,
}: {
  open: boolean;
  reasons: DismissReason[];
  busy: boolean;
  onClose: () => void;
  onConfirm: (reasonCode: string, note: string) => void;
}) {
  const [code, setCode] = useState("");
  const [note, setNote] = useState("");

  return (
    <FormDialog open={open} onClose={onClose} maxWidth="xs">
      <DialogTitle>Dismiss this diagnosis</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <Typography variant="body2" color="text.secondary">
            Telling us why is how a rule that is wrong for this business gets
            found. It is the only thing that changes what you are shown next.
          </Typography>
          <TextField
            select
            required
            label="Reason"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            sx={{ ...TOUCH }}
          >
            {reasons.map((r) => (
              <MenuItem key={r.code} value={r.code}>{r.label}</MenuItem>
            ))}
          </TextField>
          <TextField
            label="Anything else (optional)"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            multiline
            minRows={2}
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} sx={{ ...TOUCH }}>Cancel</Button>
        <Button
          variant="contained"
          disabled={!code || busy}
          onClick={() => onConfirm(code, note)}
          sx={{ ...TOUCH }}
        >
          Dismiss
        </Button>
      </DialogActions>
    </FormDialog>
  );
}
