import { useRef, useState } from "react";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogContentText from "@mui/material/DialogContentText";
import DialogTitle from "@mui/material/DialogTitle";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import MenuItem from "@mui/material/MenuItem";
import Typography from "@mui/material/Typography";
import { ErrorState, StatusChip } from "../platform/kit";
import { tokens } from "../theme";

const SAMPLE = `2001174, 20
CNMG 120408 KCP25  50
2045826 x30
XZ-CUSTOM-778-NOTREAL, 5`;

/** The closed set from `domain/enums.InboundChannel`, labelled for a person.
 *
 *  Deliberately has no "Other" or "Unknown" entry, because the enum has none:
 *  "an enquiry that arrived some other way has no honest value to store", and
 *  the channel is the one index the corpus is grouped by. Leaving it unset is
 *  the honest answer and the field says what that costs. */
const CHANNELS: [string, string][] = [
  ["EMAIL", "Email"],
  ["WHATSAPP", "WhatsApp"],
  ["PDF", "A PDF they sent"],
  ["PORTAL", "Customer portal"],
  ["PHONE_NOTE", "Phone — my note of it"],
];

/** One dialog of this kind is open at a time, so one id is enough to point the
 *  dialog at its own title. MUI does not wire the two together on its own, and
 *  without it a screen reader announces "dialog" and nothing else. */
const TITLE_ID = "intake-title";

/** Paste an RFQ and resolve it into quote lines.
 *
 * A `Dialog` rather than the hand-rolled overlay it replaces. The old one had
 * no Escape key at all — the only way out was to hit Cancel with a mouse — and
 * nothing kept focus inside it, so tabbing walked into the quote grid behind
 * while the dialog was still covering it.
 *
 * **Three inputs and one paragraph of help, in that order of weight.** The
 * guidance used to be two outlined panels below the fields — "Accepted
 * formats" as a bulleted list and "What happens next" beside it — which put
 * more boxes on this dialog than it has controls, and put the formats a
 * person needs *while typing* below the box they were typing in. The formats
 * are now the text field's own helper text, where they are read, and the
 * sample button sits under the field it fills rather than under both panels.
 */
export function IntakeModal({
  onClose,
  onSubmit,
}: {
  onClose: () => void;
  /** `file` is the document the RFQ arrived as, when the desk attached one.
   *  Handed up rather than uploaded here: this component collects, the screen
   *  that owns the quote decides what order to call the API in.
   *
   *  **Awaited.** It used to return `void`, so this dialog could not tell a
   *  request in flight from one that had never started: the button stayed
   *  live, and a double-click on "Resolve & add" fired the upload and the
   *  intake twice — two documents stored and the lines added twice over. The
   *  promise is what closes that. Resolve once the enquiry has landed and the
   *  caller closes this dialog; reject with the server's own sentence and it
   *  stays open, with the pasted text and the attachment still in it, which is
   *  the only copy of either. */
  onSubmit: (text: string, channel: string, file: File | null) => Promise<void>;
}) {
  const [text, setText] = useState("");
  // The document itself, not an id. It is uploaded by the caller when the
  // form is submitted, so closing this dialog without submitting stores
  // nothing — an upload on selection would leave a document behind every time
  // somebody changed their mind.
  const [file, setFile] = useState<File | null>(null);
  const picker = useRef<HTMLInputElement>(null);
  // Unset by default. A pre-selected channel would be this screen answering a
  // question about the customer on their behalf, and every row it wrote would
  // be filed under a route nobody chose.
  const [channel, setChannel] = useState("");
  // The press, while it is a press. Drives the button's own spinner and — the
  // point of it — the button's disabled state, so the pair of calls behind it
  // cannot be started twice.
  const [submitting, setSubmitting] = useState(false);
  // Why the last press did not land, in the server's words. Held here rather
  // than flashed past: the refusal is about the text in this box, and the box
  // is still on screen to change.
  const [failure, setFailure] = useState<string | null>(null);

  async function submit() {
    setSubmitting(true);
    setFailure(null);
    try {
      await onSubmit(text, channel, file);
      // Nothing on success: the caller closes this dialog once the lines are
      // on the quote, which is the only place that knows they are.
    } catch (e) {
      setFailure(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open
      // Not while the pair of calls is in flight. Escape and a backdrop click
      // both reach `onClose`, and closing here would throw away the pasted RFQ
      // at the moment it is least recoverable — mid-request, with no answer yet
      // about whether any of it landed.
      onClose={submitting ? undefined : onClose}
      maxWidth="sm"
      fullWidth
      aria-labelledby={TITLE_ID}
    >
      <DialogTitle id={TITLE_ID}>Paste RFQ</DialogTitle>

      {/* `dividers` rather than padding alone: three fields and an attachment
          outgrow a short window, and the rules are what say the title and the
          two buttons stay put while the middle scrolls. */}
      <DialogContent dividers>
        <Stack spacing={2}>
          {/* Above the fields, not below them. This region scrolls, and a
              refusal rendered under four controls is a refusal somebody has to
              scroll to find — after pressing a button that appeared to do
              nothing.

              `ErrorState` with `onClose` and no `onRetry`: the retry is the
              "Resolve & add" button two inches below, and a second one in here
              would be two spellings of one press. */}
          {failure && (
            <ErrorState
              title="Nothing was added to the quote"
              error={failure}
              onClose={() => setFailure(null)}
            />
          )}

          <DialogContentText>
            One request per line. The engine resolves each line into a quote-ready product.
          </DialogContentText>

          <Box>
            <TextField
              label="RFQ text"
              placeholder={SAMPLE}
              multiline
              minRows={6}
              fullWidth
              autoFocus
              value={text}
              onChange={(e) => setText(e.target.value)}
              helperText={<>
                A manufacturer code or a loose description, with the quantity
                as <code>code, qty</code> or <code>code xNN</code>.
              </>}
              // Monospace so a code lines up with the one under it, at the
              // theme's own body size and from the theme's own mono token —
              // §11: a literal here is a value that will not follow.
              slotProps={{ input: {
                sx: { typography: "body2", fontFamily: tokens.fontMono },
              } }}
            />
            {/* Under the field it fills. It used to sit below both help
                panels, four elements away from the box it writes into. */}
            <Button size="small" onClick={() => setText(SAMPLE)}>
              Use sample RFQ
            </Button>
          </Box>

          <TextField
            select
            label="How did this reach you?"
            value={channel}
            onChange={(e) => setChannel(e.target.value)}
            fullWidth
            // Both, and for the reasons `CompanyFilter.CompanySelect` sets out
            // at length — this is the third select in the app whose resting
            // state *is* the empty value. `displayEmpty` is what makes MUI
            // render that option at all (`SelectInput` skips the display
            // entirely for an unfilled value, so the box would read blank);
            // `shrink` lifts the label off it, because MUI floats the label on
            // the FormControl's *filled* state and an empty value leaves that
            // false — without it "Not stated" renders underneath "How did this
            // reach you?", and looks right only while the menu is open.
            slotProps={{ select: { displayEmpty: true }, inputLabel: { shrink: true } }}
            helperText={channel
              ? "Kept as you pasted it — spelling, casing and all — so the "
                + "resolver can be measured against what customers actually write."
              : "Optional. Say how it arrived and the wording is kept for "
                + "measuring the resolver; leave it and only the resolved lines "
                + "are stored."}
          >
            {/* The way back out. `InboundChannel` has no "Other" or "Unknown"
                member and should not gain one, so this submits the same empty
                string the field opens on — unset stays unset rather than
                becoming a sixth route. Without it a mis-chosen channel could
                only be undone by cancelling the dialog, which takes the pasted
                RFQ with it, and the harm the comment on CHANNELS names — a row
                filed under a route nobody chose — was one click away and
                irreversible. */}
            <MenuItem value="">Not stated</MenuItem>
            {CHANNELS.map(([value, label]) => (
              <MenuItem key={value} value={value}>{label}</MenuItem>
            ))}
          </TextField>

          {/* The document it arrived as. "A PDF they sent" has been a channel on
              this form since before there was anywhere to put the PDF, so the
              desk stated the route and then retyped the contents. The file is
              stored and linked to the enquiry; nothing reads it yet, and the
              helper text says so rather than implying the lines below were
              extracted from it.

              A `Paper` and not bare text because it holds controls of its own:
              a picker, the name of what was picked, and the way to take it
              back. */}
          <Paper variant="outlined" sx={{ p: 1.5 }}>
            <Stack
              direction="row"
              spacing={1.5}
              sx={{ alignItems: "center", flexWrap: "wrap" }}
            >
              {/* Both dead while the press is in flight, unlike the fields
                  above them. The caller uploads this file first, so once the
                  request is away "Remove" no longer takes anything back — a
                  control that claims an undo it does not have is worse than a
                  disabled one. */}
              <Button
                size="small"
                variant="outlined"
                disabled={submitting}
                onClick={() => picker.current?.click()}
              >
                {file ? "Choose a different file" : "Attach the document"}
              </Button>
              {file && (
                <>
                  <StatusChip label={file.name} tone="info" />
                  <Button
                    size="small"
                    color="inherit"
                    disabled={submitting}
                    onClick={() => setFile(null)}
                  >
                    Remove
                  </Button>
                </>
              )}
            </Stack>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
              Optional. The file is kept with this enquiry so anyone can open what
              the customer actually sent. It is <strong>not</strong> read — the
              lines still come from the text above.
            </Typography>
            <input
              ref={picker}
              type="file"
              hidden
              accept=".pdf,.png,.jpg,.jpeg,.xlsx,.xls,.docx,.csv,.txt"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </Paper>

          {/* What pressing the button does, said next to the button rather
              than in a panel of its own. */}
          <Typography variant="body2" color="text.secondary">
            Next: the quote grid shows the matching supply products, availability
            and the next best action for each line.
          </Typography>
        </Stack>
      </DialogContent>

      <DialogActions>
        {/* Dead while the press is in flight, because there is nothing left
            for it to cancel: the upload and the intake behind it are away, and
            a Cancel that closed the dialog would stop nothing while looking
            like it had. It comes back the moment the answer does. */}
        <Button onClick={onClose} color="inherit" disabled={submitting}>Cancel</Button>
        {/* MUI's own spinner rather than a label that changes to "Resolving…":
            §7 asks for its loading components, and a label that swaps is a
            button whose accessible name moves while it is being pressed.
            `loadingPosition="start"` keeps the word readable beside it.

            `loading` also disables the button, which is the double-click fix
            itself — the second press has nothing to land on. */}
        <Button
          variant="contained"
          disabled={!text.trim()}
          loading={submitting}
          loadingPosition="start"
          onClick={() => void submit()}
        >
          Resolve &amp; add
        </Button>
      </DialogActions>
    </Dialog>
  );
}
