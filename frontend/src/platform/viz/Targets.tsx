// What each principal expects of us, typed in.
//
// The one surface in this product that captures a number rather than reading
// one. An authorised distributor does not set its own targets — the principals
// do — and nothing in Zoho holds them, so somebody has to enter them and the
// entry has to be worth doing once a quarter rather than dreaded.
//
// **Basis is a required choice, not a default nobody reads.** A principal's
// number is usually on what you buy from them; some are on what you sell of
// theirs. Comparing one against the other is the error that surfaces when a
// quarter closes wrong, so the control is a pair of radio-style buttons with
// both meanings spelled out rather than a select defaulting to whichever came
// first.
//
// **Periods are two dates.** Principals do not agree on a financial year — an
// Indian principal's Q1 is April to June, a European parent's is January to
// March — so a quarter picker would have to guess. The quick-fill buttons
// offer the common ones and the dates stay editable.

import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";
import Typography from "@mui/material/Typography";
import { useEffect, useState } from "react";
import { useSnackbar } from "notistack";
import { money } from "../../money";
import { formatDate } from "../../when";
import { papi } from "../api";
import { DataGrid, numeric } from "../DataGrid";
import { ErrorState, InlineLink, LoadingState } from "../kit";
import type { PlatformSession } from "../types";

type Row = Record<string, unknown>;

const rows = (v: unknown): Row[] => (v as Row[] | undefined) ?? [];

const PURCHASE = "PURCHASE";
const SALES = "SALES";

/** The quarters people actually use here, offered rather than assumed. */
function quarters(today: Date): { label: string; start: string; end: string }[] {
  const y = today.getFullYear();
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  const q = (sm: number, em: number, ey: number, label: string) => ({
    label,
    start: iso(new Date(Date.UTC(sm < 4 ? y : y, sm - 1, 1))),
    end: iso(new Date(Date.UTC(ey, em, 0))),
  });
  return [
    q(4, 6, y, "Apr–Jun"),
    q(7, 9, y, "Jul–Sep"),
    q(10, 12, y, "Oct–Dec"),
    { label: "Financial year", start: `${y}-04-01`, end: `${y + 1}-03-31` },
  ];
}

export function TargetEditor({
  session, onClose, onSaved,
}: { session: PlatformSession; onClose: () => void; onSaved: () => void }) {
  const { enqueueSnackbar } = useSnackbar();
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [vendorId, setVendorId] = useState("");
  const [basis, setBasis] = useState(PURCHASE);
  const [amount, setAmount] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [note, setNote] = useState("");

  const load = () => {
    papi.targets(session.token)
      .then((d) => { setData(d); setError(null); })
      .catch((e: Error) => setError(e.message));
  };
  useEffect(load, [session.token]);

  const vendors = rows(data?.vendors);
  const existing = rows(data?.targets);
  const ready = vendorId && amount && start && end;

  const save = async () => {
    setBusy(true);
    try {
      await papi.setTarget(session.token, {
        vendor_id: vendorId, basis, amount: Number(amount),
        period_start: start, period_end: end, note: note || null,
      });
      enqueueSnackbar("Target saved", { variant: "success" });
      setAmount(""); setNote("");
      load();
      onSaved();
    } catch (e) {
      enqueueSnackbar((e as Error).message, { variant: "error" });
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: string) => {
    try {
      await papi.deleteTarget(session.token, id);
      load();
      onSaved();
    } catch (e) {
      enqueueSnackbar((e as Error).message, { variant: "error" });
    }
  };

  return (
    <Dialog open onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle sx={{ pb: 0.5 }}>
        Supplier targets
        <Typography variant="body2" sx={{ color: "text.secondary" }}>
          What each principal expects, per period. Nothing in Zoho holds these,
          so they are typed once and kept.
        </Typography>
      </DialogTitle>

      <DialogContent sx={{ pt: 2 }}>
        {error && <ErrorState error={error} onRetry={load} />}
        {!data && !error && <LoadingState rows={3} height={40} />}

        {data && (
          <>
            <Stack spacing={2}>
              <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
                <TextField select label="Supplier" value={vendorId} fullWidth
                           size="small"
                           onChange={(e) => setVendorId(e.target.value)}>
                  {vendors.map((v) => (
                    <MenuItem key={String(v.vendor_id)} value={String(v.vendor_id)}>
                      {String(v.label)}
                    </MenuItem>
                  ))}
                </TextField>
                <TextField label="Target" value={amount} size="small" fullWidth
                           type="number"
                           onChange={(e) => setAmount(e.target.value)}
                           helperText={amount ? money(Number(amount)) : " "} />
              </Stack>

              {/* Spelled out rather than a select. Comparing a purchase target
                  against a sales figure is the error that only surfaces when a
                  quarter closes wrong. */}
              <div>
                <Typography variant="overline" sx={{ color: "text.secondary" }}>
                  The target is measured on
                </Typography>
                <ToggleButtonGroup size="small" exclusive value={basis}
                                   onChange={(_, v) => { if (v) setBasis(v); }}
                                   aria-label="Target basis" sx={{ display: "flex" }}>
                  <ToggleButton value={PURCHASE} sx={{ flex: 1 }}>
                    what we buy from them
                  </ToggleButton>
                  <ToggleButton value={SALES} sx={{ flex: 1 }}>
                    what we sell of theirs
                  </ToggleButton>
                </ToggleButtonGroup>
              </div>

              <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
                <TextField label="Period start" type="date" value={start}
                           size="small" fullWidth
                           slotProps={{ inputLabel: { shrink: true } }}
                           onChange={(e) => setStart(e.target.value)} />
                <TextField label="Period end" type="date" value={end}
                           size="small" fullWidth
                           slotProps={{ inputLabel: { shrink: true } }}
                           onChange={(e) => setEnd(e.target.value)} />
              </Stack>

              <div className="mix-lines">
                {quarters(new Date()).map((q) => (
                  <li key={q.label} style={{ listStyle: "none" }}>
                    <button type="button" className="mix-line"
                            onClick={() => { setStart(q.start); setEnd(q.end); }}>
                      <span className="mix-line-body">
                        <strong>{q.label}</strong>
                        <span className="viz-muted">
                          {formatDate(q.start)} – {formatDate(q.end)}
                        </span>
                      </span>
                    </button>
                  </li>
                ))}
              </div>

              <TextField label="Note (optional)" value={note} size="small"
                         placeholder="Where this number came from"
                         onChange={(e) => setNote(e.target.value)} />
            </Stack>

            {existing.length > 0 && (
              <div className="tier3-list" style={{ marginTop: 20 }}>
                <h4>On record</h4>
                <DataGrid<Row>
                  ariaLabel="Vendor targets on record"
                  rows={existing}
                  pageSize={8}
                  filters={false}
                  getRowId={(r) => String(r.target_id)}
                  columns={[
                    { field: "vendor_label", headerName: "Supplier", flex: 1,
                      minWidth: 160 },
                    { field: "period_start", headerName: "From", width: 130, flex: 0,
                      valueFormatter: (p: { value: unknown }) =>
                        formatDate(String(p.value)) },
                    { field: "period_end", headerName: "To", width: 130, flex: 0,
                      valueFormatter: (p: { value: unknown }) =>
                        formatDate(String(p.value)) },
                    { field: "basis", headerName: "On", width: 110, flex: 0,
                      valueFormatter: (p: { value: unknown }) =>
                        String(p.value) === SALES ? "sales" : "purchases" },
                    numeric<Row>("amount", "Target", (v) => money(v),
                                 { width: 150, flex: 0 }),
                    {
                      colId: "remove", headerName: "", width: 90, flex: 0,
                      cellRenderer: (p: { data: Row }) => (
                        <InlineLink onClick={() => remove(String(p.data.target_id))}>
                          Remove
                        </InlineLink>
                      ),
                    },
                  ]}
                />
              </div>
            )}
          </>
        )}
      </DialogContent>

      <DialogActions>
        <Button onClick={onClose} color="inherit">Close</Button>
        <Button variant="contained" disabled={!ready || busy} onClick={save}>
          {busy ? "Saving…" : "Save target"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
