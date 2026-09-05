// Which company is this quote raised from?
//
// Asked only when there is a real choice. An organization reading one
// company's books never sees this dialog — the server answers without being
// told, and a picker with one option is a question with one answer.
//
// It is not cosmetic. Each connected company decodes its own item master, so
// the company decides which catalogue every line on the quote resolves
// against; answering from the wrong one would put another company's product on
// a customer's quote with a real provenance stamp behind it. That is why the
// server refuses rather than picking, and why this dialog has no default
// selection.
//
// **And why nothing here takes focus, either.** The obvious keyboard nicety —
// focus the first company so Enter answers the dialog — is that same refusal
// undone one layer further out: an Enter pressed on the way somewhere else
// would pick a company nobody chose, and it would look identical afterwards to
// one somebody did. The focus trap starts on the dialog and Tab reaches the
// options, which costs one keystroke and cannot answer the question by
// accident. Everywhere else on the way into a quote — the customer search
// next door — the fastest keyboard path is the right one; this is the one
// control where a keystroke saved is a keystroke that answers for somebody.

import { useId, useState } from "react";
import Button from "@mui/material/Button";
import CircularProgress from "@mui/material/CircularProgress";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogContentText from "@mui/material/DialogContentText";
import DialogTitle from "@mui/material/DialogTitle";
import LinearProgress from "@mui/material/LinearProgress";
import List from "@mui/material/List";
import ListItemButton from "@mui/material/ListItemButton";
import ListItemText from "@mui/material/ListItemText";

import type { QuoteCompany } from "../api";

export function CompanyPicker({
  open, companies, customer, busy = false, onPick, onCancel,
}: {
  open: boolean;
  companies: QuoteCompany[];
  /** Who the quote is for, so the question reads as one step of the same
   *  action rather than as an unexplained second dialog. */
  customer: string;
  busy?: boolean;
  onPick: (connectionId: string) => void;
  onCancel: () => void;
}) {
  // Only the description. MUI already wires `aria-labelledby` to the
  // `DialogTitle` through `DialogContext` — restating it here would be a
  // second id to keep in step for no gain — but it wires nothing to the
  // sentence that explains what the choice does, and that sentence is the
  // reason this dialog is not just a list of two words. Generated rather than
  // written out, because the Quote Builder and the workspace each mount their
  // own and a duplicated `id` resolves to whichever came first.
  const noteId = useId();
  // Which row was pressed. Feedback only, and read only while `busy`: it
  // preselects nothing, and it survives no reopen because both call sites
  // mount this dialog conditionally. Without it a press answers by greying
  // every option out at once — that says work is happening, but not which
  // company it is happening for, which is the one thing the reader wants
  // confirmed at exactly that moment.
  const [taken, setTaken] = useState<string | null>(null);

  return (
    <Dialog
      open={open} onClose={onCancel} fullWidth maxWidth="xs"
      aria-describedby={noteId}
    >
      <DialogTitle>Which company is this quote from?</DialogTitle>
      {/* MUI's own progress rather than a dialog that just goes quiet and
          grey. The quote is created while this stays open, so the wait is a
          state the screen has to show (§7) — and it sits under the title,
          where it reads as "this dialog is working" rather than as one row's
          problem. */}
      {busy && <LinearProgress />}
      <DialogContent>
        <DialogContentText id={noteId} sx={{ mb: 1 }}>
          {customer ? `The quote for ${customer}` : "This quote"} will be
          raised from one of your companies. Each keeps its own item master, so
          this choice decides which catalogue every line on the quote resolves
          against.
        </DialogContentText>
        <List dense disablePadding>
          {companies.map((c) => (
            <ListItemButton
              key={c.connection_id}
              disabled={busy}
              onClick={() => { setTaken(c.connection_id); onPick(c.connection_id); }}
              // `dense` keeps the padding tight, so three companies still
              // read as one short list. The 44px thumb floor underneath it is
              // `theme.ts`'s `MuiListItemButton` default now rather than a
              // `kit.TOUCH` spread written out here — a floor each call site
              // has to remember is exactly how it went missing from
              // `CompanyScope`. The two are not in conflict: one sets the
              // padding, the other a minimum the padding cannot fall below.
              sx={{ gap: 1 }}
            >
              <ListItemText
                primary={c.label || c.connection_id}
                // The id *under* the name rather than instead of it. Two
                // connections can legitimately carry the same label — the same
                // ERP connected twice, two books named for the same city — and
                // then this is the only thing that tells them apart. Where
                // there is no label it is the whole answer, which is why it
                // moves up to `primary` in that case.
                secondary={c.label ? c.connection_id : undefined}
              />
              {busy && taken === c.connection_id && (
                <CircularProgress size={16} aria-label="Starting the quote" />
              )}
            </ListItemButton>
          ))}
        </List>
      </DialogContent>
      <DialogActions>
        <Button onClick={onCancel} disabled={busy}>Cancel</Button>
      </DialogActions>
    </Dialog>
  );
}
