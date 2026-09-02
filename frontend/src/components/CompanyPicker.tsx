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

import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogContentText from "@mui/material/DialogContentText";
import DialogTitle from "@mui/material/DialogTitle";
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
  return (
    <Dialog open={open} onClose={onCancel} fullWidth maxWidth="xs">
      <DialogTitle>Which company is this quote from?</DialogTitle>
      <DialogContent>
        <DialogContentText sx={{ mb: 1 }}>
          {customer ? `The quote for ${customer}` : "This quote"} will be raised from one of your companies,
          and its lines are decoded against that company&apos;s own item master.
        </DialogContentText>
        <List dense>
          {companies.map((c) => (
            <ListItemButton key={c.connection_id} disabled={busy}
                            onClick={() => onPick(c.connection_id)}>
              <ListItemText primary={c.label || c.connection_id}
                            secondary={c.label ? c.connection_id : undefined} />
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
