import { useState } from "react";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

const SAMPLE = `2001174, 20
CNMG 120408 KCP25  50
2045826 x30
XZ-CUSTOM-778-NOTREAL, 5`;

/** Paste an RFQ and resolve it into quote lines.
 *
 * A `Dialog` rather than the hand-rolled overlay it replaces. The old one had
 * no Escape key at all — the only way out was to hit Cancel with a mouse — and
 * nothing kept focus inside it, so tabbing walked into the quote grid behind
 * while the dialog was still covering it. */
export function IntakeModal({
  onClose,
  onSubmit,
}: {
  onClose: () => void;
  onSubmit: (text: string) => void;
}) {
  const [text, setText] = useState("");
  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ pb: 1 }}>
        Paste RFQ
        <Typography component="div" variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
          One request per line. The engine resolves each line into a quote-ready product.
        </Typography>
      </DialogTitle>

      <DialogContent sx={{ pt: 2 }}>
        <TextField
          label="RFQ text"
          placeholder={SAMPLE}
          multiline
          minRows={6}
          fullWidth
          autoFocus
          value={text}
          onChange={(e) => setText(e.target.value)}
          slotProps={{ input: { sx: { fontFamily: "ui-monospace, monospace", fontSize: 13 } } }}
        />

        <Stack
          direction={{ xs: "column", sm: "row" }}
          spacing={1.5}
          sx={{ mt: 2 }}
        >
          <Paper variant="outlined" sx={{ p: 1.5, flex: 1 }}>
            <Typography variant="subtitle2" sx={{ mb: 0.5 }}>Accepted formats</Typography>
            <Typography component="ul" variant="body2" color="text.secondary" sx={{ m: 0, pl: 2.2 }}>
              <li>Manufacturer code</li>
              <li>Loose description</li>
              <li>Quantity as <code>code, qty</code> or <code>code xNN</code></li>
            </Typography>
          </Paper>
          <Paper variant="outlined" sx={{ p: 1.5, flex: 1 }}>
            <Typography variant="subtitle2" sx={{ mb: 0.5 }}>What happens next</Typography>
            <Typography variant="body2" color="text.secondary">
              The quote grid will show matching supply products, availability, and the next best action.
            </Typography>
          </Paper>
        </Stack>

        <Button
          size="small"
          sx={{ mt: 1.5 }}
          onClick={() => setText(SAMPLE)}
        >
          Use sample RFQ
        </Button>
      </DialogContent>

      <DialogActions>
        <Button onClick={onClose} color="inherit">Cancel</Button>
        <Button
          variant="contained"
          disabled={!text.trim()}
          onClick={() => onSubmit(text)}
        >
          Resolve &amp; add
        </Button>
      </DialogActions>
    </Dialog>
  );
}
