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
 *
 * **An empty list says why it is empty.** This dialog is the only way into the
 * Quote Builder, so a directory with nothing in it is not a quiet edge case —
 * it is a screen nobody can get past. It used to answer "Start typing a name."
 * to every one of: the request failed, the organization has never synced, every
 * customer on file is dormant, and a salesperson holds no accounts. Four facts,
 * one reassuring sentence, and only one of them is fixed by typing. `kit.tsx`
 * states the rule this broke — a screen that repeats one generic sentence for
 * every reason is a screen nobody trusts.
 */
import { useEffect, useMemo, useState } from "react";
import Autocomplete from "@mui/material/Autocomplete";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import CircularProgress from "@mui/material/CircularProgress";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogContentText from "@mui/material/DialogContentText";
import DialogTitle from "@mui/material/DialogTitle";
import TextField from "@mui/material/TextField";
import { useNavigate } from "react-router-dom";

import { papi } from "../platform/api";
import { optionLabel } from "../platform/EntityName";
import { abilityFor } from "../platform/ability";
import { EmptyState, ErrorState } from "../platform/kit";
import { PATH } from "../platform/route";
import type { Account, PlatformSession } from "../platform/types";

export interface PickedCustomer {
  id: string;
  name: string;
}

/** What an empty directory means, in the words of the reason it is empty.
 *
 *  Split out because the wording is the substance of this fix and reads better
 *  as prose than as three nested ternaries inside JSX.
 *
 *  The salesperson case is the one worth being careful about. `routers/accounts`
 *  narrows the list to accounts they own and answers with an empty list rather
 *  than an error — deliberately, so a dropdown does not break — which means a
 *  salesperson's empty directory is genuinely two facts the client cannot tell
 *  apart: nothing synced, or nothing assigned. Naming both is honest; picking
 *  one would be the same guess this component used to make. */
function emptyCopy(reason: EmptyReason, isSalesperson: boolean):
{ title: string; reason: string } {
  if (reason === "INACTIVE_ONLY") {
    return {
      title: "Every customer on file is inactive",
      reason:
        "Customers were synced, but Zoho marks all of them inactive and a quote "
        + "opens against an active account. Reactivate the customer in Zoho and "
        + "sync again.",
    };
  }
  if (isSalesperson) {
    return {
      title: "No accounts are assigned to you yet",
      reason:
        "A quote is priced from the customer's own history, so you can only "
        + "quote accounts assigned to you. Ask an owner to assign you one — or, "
        + "if this organization was only just connected, to run the first sync.",
    };
  }
  return {
    title: "No customers have been synced yet",
    reason:
      "The customer directory is filled by the Zoho sync. Until the first one "
      + "completes there is nobody to quote against.",
  };
}

/** Why the directory came back with nothing in it.
 *
 *  `null` means it did not — either there are rows, or the query simply matched
 *  none of them, which the Autocomplete's own `noOptionsText` already says.
 *  The two values are distinguished by asking a second question, because the
 *  answer changes what the reader should go and do: a sync fills an empty
 *  directory, and nothing fills one whose every row is dormant. */
type EmptyReason = "NONE" | "INACTIVE_ONLY" | null;

export function CustomerPicker({
  open, session, title, note, busy = false, onPick, onCancel,
}: {
  open: boolean;
  /** The platform session. Passed in rather than read from storage: the Quote
   *  Builder runs on the platform's own session now, so its caller already
   *  holds this and a second source would be one to keep in step. The whole
   *  session rather than the token alone, because an empty list means something
   *  different to a salesperson — see `emptyCopy`. */
  session: PlatformSession;
  title: string;
  /** Why this is being asked, when the answer is not obvious. */
  note?: string;
  /** The quote is being created. The dialog stays up until it lands, so this
   *  is what stops a second press creating a second quote. */
  busy?: boolean;
  onPick: (c: PickedCustomer) => void;
  onCancel?: () => void;
}) {
  const token = session.token;
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState<Account[]>([]);
  const [loading, setLoading] = useState(false);
  const [choice, setChoice] = useState<Account | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [emptyReason, setEmptyReason] = useState<EmptyReason>(null);
  // Bumped to re-run the load. A failure here used to be terminal: the effect
  // re-runs on the query, and the one person who cannot usefully type is the
  // one looking at "could not load customers".
  const [reloads, setReloads] = useState(0);

  useEffect(() => {
    if (!open || !token) return;
    let live = true;
    setLoading(true);
    // Debounced: a keystroke per request would put three hundred queries
    // through a search that reads the whole customer table.
    const t = setTimeout(() => {
      papi.listAccounts(token, query)
        .then(async (r) => {
          if (!live) return;
          setRows(r);
          setFailed(null);
          if (r.length || query.trim()) {
            setEmptyReason(null);
            return;
          }
          // Asked only when the unfiltered list came back empty, which is the
          // one case it can tell apart: "nothing has been synced" and
          // "everything on file is dormant" both show up here as zero active
          // rows, and asserting the first when it is the second would replace
          // one wrong sentence with another. A failure to find out leaves it
          // unsaid rather than guessed.
          const all = await papi.listAccounts(token, "", "all").catch(() => null);
          if (live) {
            setEmptyReason(all === null ? null : all.length ? "INACTIVE_ONLY" : "NONE");
          }
        })
        .catch((e) => { if (live) setFailed((e as Error).message); })
        .finally(() => { if (live) setLoading(false); });
    }, 220);
    return () => { live = false; clearTimeout(t); };
  }, [open, token, query, reloads]);

  // Whether to print the company beside each name. With one connected book
  // every line would say the same thing, which is width spent saying nothing.
  const showSource = useMemo(
    () => rows.some((r) => r.sources_differ), [rows]);

  // Manager or owner. The same pair `require_manager_or_owner` guards the
  // connection screen with, so the button is offered to the people who can
  // actually use what it opens — a salesperson is told who to ask instead.
  const canSetUp = abilityFor(session).can("read", "policy");
  // There is nothing to search. Either way the panel below carries the whole
  // answer, and a search field over a directory that cannot be searched is
  // furniture somebody will type into and get nothing back from.
  const nothingToSearch = Boolean(failed) || Boolean(emptyReason);

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
          // No failure branch: a load that failed hides this field and says so
          // underneath, and the same sentence in two places is one to keep in
          // step. What is left is the one thing this text can answer.
          noOptionsText={query ? "No customer matches that." : "Start typing a name."}
          sx={{ display: nothingToSearch ? "none" : undefined }}
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
        {failed && (
          <Box sx={{ mt: 2 }}>
            <ErrorState
              title="Customers could not be loaded"
              error={failed}
              onRetry={() => setReloads((n) => n + 1)}
              busy={loading}
            />
          </Box>
        )}
        {!failed && !loading && emptyReason && (
          <EmptyState
            {...emptyCopy(emptyReason, session.role === "SALESPERSON")}
            action={canSetUp && emptyReason === "NONE" ? (
              <Button variant="contained" onClick={() => navigate(PATH.data)}>
                Data &amp; connection
              </Button>
            ) : undefined}
          />
        )}
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
