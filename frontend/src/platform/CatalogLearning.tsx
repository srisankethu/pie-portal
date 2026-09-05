// What the system has learned from quoting, and how the suggestion layers are
// doing — two panels beneath the catalogues on the Decoded catalogue screen.
//
// "What the system remembers" is every remembered phrase for this
// organization's customers, with a Retire action for an owner: the eraser for a
// memory that was wrong. A retired phrase is deactivated, never deleted — the
// quote it came from still cites it.
//
// "Suggestions at work" is the report the next investment decisions rest on,
// counted by the server from stored quotes (`app/retrieval/report.py`). The
// thresholds are printed as the server's own readings rather than judged here,
// so the screen shows the numbers and the sentence, and decides nothing.
import { useCallback, useEffect, useState } from "react";
import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogContentText from "@mui/material/DialogContentText";
import DialogTitle from "@mui/material/DialogTitle";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";

import { papi } from "./api";
import type { ColDef } from "./DataGrid";
import { DataGrid } from "./DataGrid";
import { EmptyState } from "./kit";
import type { PhraseAlias, PhraseAliases, PlatformSession, RetrievalReport } from "./types";
import { Bp, Labelled } from "./ui";
import { formatDateTime } from "../when";
import { pct as fmtPct } from "./format";

/** Whole percent here, not one decimal: this is a match score on a learned
 *  phrase, and "83.4%" claims a precision the scorer does not have. The shared
 *  formatter with `digits: 0`, rather than a sixth copy of the arithmetic. */
function pct(v: number | null): string {
  return fmtPct(v, 0);
}

export function CatalogLearning({ session }: { session: PlatformSession }) {
  const [aliases, setAliases] = useState<PhraseAliases | null>(null);
  const [report, setReport] = useState<RetrievalReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retiring, setRetiring] = useState<PhraseAlias | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [a, r] = await Promise.all([
        papi.phraseAliases(session.token),
        papi.retrievalReport(session.token),
      ]);
      setAliases(a);
      setReport(r);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [session.token]);

  useEffect(() => {
    load();
  }, [load]);

  const retire = async () => {
    if (!retiring) return;
    setBusy(true);
    try {
      await papi.retirePhraseAlias(session.token, retiring.alias_id);
      setRetiring(null);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const columns: ColDef<PhraseAlias>[] = [
    { field: "customer", headerName: "Customer", flex: 1, minWidth: 140 },
    { field: "phrase", headerName: "They asked for", flex: 2, minWidth: 200 },
    { field: "target_record_id", headerName: "Quoted as", minWidth: 120 },
    {
      field: "created_at", headerName: "Remembered", minWidth: 150,
      valueFormatter: (p: { value: string }) => formatDateTime(p.value),
    },
    { field: "source_ref", headerName: "From", minWidth: 160 },
    ...(aliases?.can_manage ? [{
      field: "alias_id", headerName: "", width: 96, sortable: false, filter: false,
      cellRenderer: (p: { data: PhraseAlias }) => (
        <Button size="small" onClick={() => setRetiring(p.data)}
                title="Stop offering this remembered phrase">
          Retire
        </Button>
      ),
    } as ColDef<PhraseAlias>] : []),
  ];

  return (
    <div style={{ marginTop: 24 }}>
      <h3 className="st-h3">What the system remembers</h3>
      <p className="st-help">
        Every time a product is put on a quote for a linked customer, the words
        they asked for are remembered and offered back — as a suggestion,
        never a selection — the next time they ask for something close.
        {aliases?.can_manage
          ? " Retire one that was wrong; choosing again for the same words also replaces it."
          : " Retiring one is an owner action."}
      </p>
      {error && <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert>}
      {aliases === null ? (
        <Skeleton variant="rectangular" height={120} />
      ) : (
        <DataGrid<PhraseAlias>
          rows={aliases.aliases}
          columns={columns}
          getRowId={(r) => r.alias_id}
          pageSize={10}
          filters={aliases.aliases.length > 10}
          ariaLabel="Remembered phrases"
          empty={<EmptyState
            title="Nothing remembered yet"
            reason="Phrases are remembered as people select supply on quotes for linked customers. Past quotes can be replayed with the backfill command."
          />}
          renderNarrow={(r) => (
            <Stack spacing={0.5} sx={{ p: 1.5 }}>
              <b>{r.customer}</b>
              <span>“{r.phrase}” → {r.target_record_id}</span>
              <span className="fsrc">{formatDateTime(r.created_at)} · {r.source_ref}</span>
              {aliases.can_manage && (
                <Button size="small" onClick={() => setRetiring(r)}>Retire</Button>
              )}
            </Stack>
          )}
        />
      )}

      <h3 className="st-h3" style={{ marginTop: 24 }}>Suggestions at work</h3>
      {report === null ? (
        <Skeleton variant="rectangular" height={100} />
      ) : (
        <Bp style={{ padding: "14px" }}>
          <table className="facts">
            <tbody>
              <tr>
                <td>
                  <Labelled tip="Customer-linked lines the engine read as words rather than a code, across every stored quote.">
                    Lines counted
                  </Labelled>
                </td>
                <td className="fv">{report.counts.lines}</td>
              </tr>
              <tr>
                <td>
                  <Labelled tip="Of the choices a person made, how many took a product found beneath the engine's ranking — by description, a confirmed code or a remembered phrase. Past a fifth, the ranking is the bottleneck.">
                    Found beneath the ranking
                  </Labelled>
                </td>
                <td className="fv">
                  {pct(report.shares.found_beneath_ranking)}
                  <div className="fsrc">
                    {report.counts.chosen_from_retrieval + report.counts.chosen_from_confirmed_code
                      + report.counts.chosen_from_phrase} of {report.counts.chosen_by_person} choices
                  </div>
                </td>
              </tr>
              <tr>
                <td>
                  <Labelled tip="Of the choices a person made, how many were typed in with nothing offered. Past a fifth, the gap is meaning rather than spelling.">
                    Typed in, nothing offered
                  </Labelled>
                </td>
                <td className="fv">
                  {pct(report.shares.typed_unoffered)}
                  <div className="fsrc">{report.counts.typed_unoffered} of {report.counts.chosen_by_person} choices</div>
                </td>
              </tr>
              <tr>
                <td>
                  <Labelled tip="Remembered phrases and confirmed codes, and how many customers they cover. Two thousand phrases is the floor for training a meaning-aware model on them.">
                    Learned so far
                  </Labelled>
                </td>
                <td className="fv">
                  {report.learned.phrase_aliases} phrases · {report.learned.confirmed_codes} codes
                  <div className="fsrc">
                    across {report.learned.customers_with_aliases} customers
                    {" "}· training floor {report.learned.training_pairs_floor}
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
          {report.readings.map((r) => (
            <p key={r} className="st-help" style={{ marginTop: 6 }}>{r}</p>
          ))}
        </Bp>
      )}

      <Dialog open={retiring !== null} onClose={() => !busy && setRetiring(null)}>
        <DialogTitle>Stop offering this?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            “{retiring?.phrase}” will no longer suggest {retiring?.target_record_id} for
            {" "}{retiring?.customer}. The quote it came from is unchanged, and the
            memory is kept on record as retired.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRetiring(null)} disabled={busy}>Keep</Button>
          <Button variant="contained" color="warning" onClick={retire} disabled={busy}>
            Retire
          </Button>
        </DialogActions>
      </Dialog>
    </div>
  );
}
