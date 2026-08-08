/** Choose which customer a quote is for.
 *
 * There was no way to. The Quote Builder created its quote with the literal
 * string "Pitti Engineering Ltd" on sign-in, so every quote in the product was
 * for one customer and the header's "Customer" was a label rather than a
 * control.
 *
 * **It carries the id, not just the name.** "ABC Industries" can exist in all
 * three connected books and be three different customers — the reason
 * `domain/origin.py` exists at all. `_resolve_customer` tries the id before any
 * name match, so passing it turns a tolerant guess into an exact lookup. The
 * name still travels, because that is what the header prints and what Zoho
 * wants on the estimate.
 *
 * Search runs on the server. Three thousand customers is past the point where
 * shipping the list and filtering it here is reasonable, and `/api/v1/accounts`
 * already takes a `q`.
 */
import { useEffect, useMemo, useState } from "react";
import Autocomplete from "@mui/material/Autocomplete";
import Button from "@mui/material/Button";
import CircularProgress from "@mui/material/CircularProgress";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogContentText from "@mui/material/DialogContentText";
import DialogTitle from "@mui/material/DialogTitle";
import TextField from "@mui/material/TextField";

import { papi } from "../platform/api";
import { optionLabel } from "../platform/EntityName";
import type { Account } from "../platform/types";
import { platformToken } from "../intelligence";

export interface PickedCustomer {
  id: string;
  name: string;
}

export function CustomerPicker({
  open, title, note, busy = false, onPick, onCancel,
}: {
  open: boolean;
  title: string;
  /** Why this is being asked, when the answer is not obvious. */
  note?: string;
  /** The quote is being created. The dialog stays up until it lands, so this
   *  is what stops a second press creating a second quote. */
  busy?: boolean;
  onPick: (c: PickedCustomer) => void;
  onCancel?: () => void;
}) {
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState<Account[]>([]);
  const [loading, setLoading] = useState(false);
  const [choice, setChoice] = useState<Account | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const token = platformToken();

  useEffect(() => {
    if (!open || !token) return;
    let live = true;
    setLoading(true);
    // Debounced: a keystroke per request would put three hundred queries
    // through a search that reads the whole customer table.
    const t = setTimeout(() => {
      papi.listAccounts(token, query)
        .then((r) => { if (live) { setRows(r); setFailed(null); } })
        .catch((e) => { if (live) setFailed((e as Error).message); })
        .finally(() => { if (live) setLoading(false); });
    }, 220);
    return () => { live = false; clearTimeout(t); };
  }, [open, token, query]);

  // Whether to print the company beside each name. With one connected book
  // every line would say the same thing, which is width spent saying nothing.
  const showSource = useMemo(
    () => rows.some((r) => r.sources_differ), [rows]);

  if (!token) {
    return (
      <Dialog open={open} onClose={onCancel} maxWidth="sm" fullWidth>
        <DialogTitle>{title}</DialogTitle>
        <DialogContent>
          <DialogContentText>
            The customer list comes from the platform, and this browser has no
            platform sign-in. Sign in to the Decision Platform in this browser,
            then reopen the Quote Builder.
          </DialogContentText>
        </DialogContent>
        {onCancel && (
          <DialogActions><Button onClick={onCancel}>Close</Button></DialogActions>
        )}
      </Dialog>
    );
  }

  return (
    <Dialog open={open} onClose={onCancel} maxWidth="sm" fullWidth>
      <DialogTitle>{title}</DialogTitle>
      <DialogContent>
        {note && (
          <DialogContentText sx={{ mb: 2 }}>{note}</DialogContentText>
        )}
        <Autocomplete<Account>
          autoFocus
          options={rows}
          value={choice}
          loading={loading}
          onChange={(_e, v) => setChoice(v)}
          inputValue={query}
          onInputChange={(_e, v) => setQuery(v)}
          // The server already ranked and filtered; re-filtering here would
          // hide rows it deliberately returned.
          filterOptions={(x) => x}
          getOptionLabel={(o) => optionLabel(o.name, o.origin, showSource)}
          isOptionEqualToValue={(a, b) => a.customer_id === b.customer_id}
          noOptionsText={
            failed ? `Could not load customers — ${failed}`
              : query ? "No customer matches that." : "Start typing a name."}
          renderInput={(params) => (
            <TextField
              {...params}
              label="Customer"
              placeholder="Search by name"
              // MUI v9 moved these under `slotProps.input`; the v6-era
              // `params.InputProps` no longer exists on the params type.
              slotProps={{
                ...params.slotProps,
                input: {
                  ...params.slotProps.input,
                  endAdornment: (
                    <>
                      {loading && <CircularProgress size={16} />}
                      {params.slotProps.input.endAdornment}
                    </>
                  ),
                },
              }}
            />
          )}
        />
      </DialogContent>
      <DialogActions>
        {onCancel && <Button onClick={onCancel} disabled={busy}>Cancel</Button>}
        <Button
          variant="contained"
          disabled={!choice || busy}
          startIcon={busy ? <CircularProgress size={14} color="inherit" /> : undefined}
          onClick={() => choice && onPick({ id: choice.customer_id, name: choice.name })}
        >
          {busy ? "Starting the quote…" : "Use this customer"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
