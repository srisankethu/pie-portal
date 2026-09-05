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
import { StatusChip } from "../platform/kit";
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
   *  that owns the quote decides what order to call the API in and owns the
   *  error surface for both calls. */
  onSubmit: (text: string, channel: string, file: File | null) => void;
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
  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth aria-labelledby={TITLE_ID}>
      <DialogTitle id={TITLE_ID}>Paste RFQ</DialogTitle>

      {/* `dividers` rather than padding alone: three fields and an attachment
          outgrow a short window, and the rules are what say the title and the
          two buttons stay put while the middle scrolls. */}
      <DialogContent dividers>
        <Stack spacing={2}>
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
            helperText={channel
              ? "Kept as you pasted it — spelling, casing and all — so the "
                + "resolver can be measured against what customers actually write."
              : "Optional. Say how it arrived and the wording is kept for "
                + "measuring the resolver; leave it and only the resolved lines "
                + "are stored."}
          >
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
              <Button
                size="small"
                variant="outlined"
                onClick={() => picker.current?.click()}
              >
                {file ? "Choose a different file" : "Attach the document"}
              </Button>
              {file && (
                <>
                  <StatusChip label={file.name} tone="info" />
                  <Button size="small" color="inherit" onClick={() => setFile(null)}>
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
        <Button onClick={onClose} color="inherit">Cancel</Button>
        <Button
          variant="contained"
          disabled={!text.trim()}
          onClick={() => onSubmit(text, channel, file)}
        >
          Resolve &amp; add
        </Button>
      </DialogActions>
    </Dialog>
  );
}
