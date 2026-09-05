// Every row a pull could not fully resolve — as a grid, and as a file.
//
// This replaces a hand-written `<table>` over `skipped_sample`, which was a
// twenty-row preview of a problem that ran to 1,304. The screen said "first 20
// of 1304" honestly, and that was the whole trouble: a gap a person can see and
// cannot work reads, after the third time of looking at it, as a gap nobody can
// do anything about. Sorting, filtering by reason or supplier, and an export to
// reconcile against Zoho are what turn it back into a list of tasks.
//
// The export is fetched from the server rather than assembled from what the
// grid is showing. Building a CSV out of the visible page would reproduce the
// original defect one level up — a file that calls itself the export and is a
// page of it.
//
// **How complete this list is, is the panel's headline fact.** It used to be a
// clause in a caption beside the heading, in the same ink as the tooltip
// alongside it — which is the honest sentence rendered as decoration. It is now
// a `StatusChip` as well, because that is the difference §6 draws: a chip
// carries a shape and a word, and it is what a person sees before they read
// anything. "ALL 1,304" and "20 OF 1,304" must never look the same, and two
// tones and two words is the version of that claim that survives being skimmed.

import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Button from "@mui/material/Button";
import Typography from "@mui/material/Typography";

import { count, money } from "../money";
import { papi } from "./api";
import { DataGrid, numeric, type ColDef } from "./DataGrid";
import { saveBlob } from "./download";
import { SectionHeader, StatusChip } from "./kit";
import { Bp } from "./ui";
import type { SkippedRow, SyncRun } from "./types";

/** The second line of a grid cell or a card: what the row is, after what it is.
 *
 *  This was `className="fsrc"`, in five places here and fifty-five across
 *  `platform/`, and `styles.css` declares `.fsrc` only inside `.facttable`,
 *  `.sync-opts` and `.cx-add` — so inside an AG Grid cell it matched nothing
 *  and both lines of a two-line row rendered at body size in body ink. The
 *  height `twoLineRows` reserves is for a hierarchy that was not being drawn.
 *  Theme tokens through `Typography`, per ui-standards §11; local, because
 *  `kit.tsx` is not this change's to edit and a fix for all fifty-five belongs
 *  there rather than copied into each screen. */
function Meta({ children }: { children: ReactNode }) {
  return (
    <Typography variant="caption" component="div" color="text.secondary">
      {children}
    </Typography>
  );
}

/** The sample the run row carries, in the shape of a full row.
 *
 *  So there is one row type and one column set rather than two tables that
 *  drift apart. What the sample cannot supply reads as "—", which is the true
 *  statement: the server did not send it, either because the run predates the
 *  full list or because the reader may not see the money on it. */
function fromSample(run: SyncRun): SkippedRow[] {
  return (run.skipped_sample || []).map((r, i): SkippedRow => {
    const ctx = (r.context || {}) as Record<string, unknown>;
    const str = (k: string) => (ctx[k] == null ? null : String(ctx[k]));
    const num = (k: string) => (ctx[k] == null ? null : Number(ctx[k]));
    return {
      skip_id: `sample-${i}`,
      seq: i,
      connection_id: null,
      company: "",
      kind: r.kind,
      code: r.code,
      detail: r.detail,
      ref: r.ref,
      missing_id: str("missing_id"),
      label: str("label"),
      sku: str("sku"),
      document: str("document"),
      document_date: str("document_date"),
      party: str("party"),
      qty: num("qty"),
      line_value: num("line_value"),
      fix: str("fix"),
    };
  });
}

const COLUMNS: ColDef<SkippedRow>[] = [
  {
    headerName: "Document", flex: 1.1, minWidth: 190,
    filter: "agTextColumnFilter",
    valueGetter: (p) => p.data?.document || p.data?.ref || "",
    cellRenderer: (p: { data?: SkippedRow }) => p.data ? (
      <div>
        <b>{p.data.document || p.data.ref}</b>
        <Meta>
          {p.data.document_date || "—"}
          {p.data.party ? ` · ${p.data.party}` : ""}
        </Meta>
      </div>
    ) : null,
  },
  {
    // The name off the *document line*, not the master — the master is the
    // thing that has no such record, so the line is the only place the name
    // survives, and the only thing anybody can search Zoho by.
    headerName: "Item on the document", flex: 1.2, minWidth: 200,
    filter: "agTextColumnFilter",
    valueGetter: (p) => p.data?.label || p.data?.sku || p.data?.missing_id || "",
    cellRenderer: (p: { data?: SkippedRow }) => p.data ? (
      <div>
        <b>{p.data.label || p.data.sku || "Not named on the line"}</b>
        <Meta>
          {p.data.sku ? `SKU ${p.data.sku} · ` : ""}
          {p.data.missing_id ? `id ${p.data.missing_id}` : p.data.ref}
        </Meta>
      </div>
    ) : null,
  },
  {
    headerName: "Why", flex: 1, minWidth: 190, filter: "agTextColumnFilter",
    valueGetter: (p) => p.data?.code || "",
    cellRenderer: (p: { data?: SkippedRow }) => p.data ? (
      <div>
        <b>{p.data.code}</b>
        <Meta>{p.data.detail}</Meta>
      </div>
    ) : null,
  },
  {
    field: "kind", headerName: "Kind", width: 130, flex: 0,
    filter: "agTextColumnFilter",
    valueFormatter: (p) => String(p.value || "").replace(/_/g, " "),
  },
  {
    field: "company", headerName: "Company", width: 150, flex: 0,
    filter: "agTextColumnFilter",
    valueFormatter: (p) => p.value || "—",
  },
  numeric<SkippedRow>("qty", "Qty", (n) => String(n), { width: 100, flex: 0 }),
  numeric<SkippedRow>("line_value", "Line value", money, { width: 140, flex: 0 }),
];

export function SkippedRowsPanel({ token, run, canExport }: {
  token: string;
  run: SyncRun;
  /** Whether this reader may fetch the full list. A skipped bill line's value
   *  is a purchase value, so the server serves it to managers and owners only;
   *  everyone else sees the sample the run row already carried. */
  canExport: boolean;
}) {
  const [rows, setRows] = useState<SkippedRow[] | null>(null);
  const [incomplete, setIncomplete] = useState<string | null>(null);
  /** Two failures, not one. The list failing to load means what is on screen is
   *  the sample standing in for it; the export failing means the file did not
   *  download and the grid is untouched. One `error` said both in one sentence,
   *  and whichever sentence it chose was wrong half the time. */
  const [loadError, setLoadError] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const runId = run.sync_run_id;

  useEffect(() => {
    if (!canExport) {
      setRows(fromSample(run));
      return;
    }
    let live = true;
    papi.syncSkipped(token, runId).then((r) => {
      if (!live) return;
      setRows(r.rows);
      setIncomplete(r.incomplete);
    }).catch((e) => {
      if (!live) return;
      // The sample is still worth showing, but not silently in place of the
      // full list — an apparently complete twenty-row table is the failure
      // this panel exists to end.
      setRows(fromSample(run));
      setLoadError((e as Error).message);
    });
    return () => { live = false; };
  }, [token, runId, canExport, run]);

  const exportCsv = useCallback(async () => {
    setSaving(true);
    setExportError(null);
    try {
      const { blob, filename } = await papi.syncSkippedCsv(token, runId);
      saveBlob(blob, filename);
    } catch (e) {
      setExportError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }, [token, runId]);

  const held = rows?.length ?? 0;
  const total = run.skipped_count;
  // Said plainly, because it is the number the whole panel is about. A reader
  // who may not fetch the list is never "complete", however the counts land:
  // what they hold is the run row's sample.
  const complete = canExport && held === total;

  return (
    <>
      <SectionHeader
        level="widget"
        title="Skipped rows"
        tip="Every document line this pull could not fully resolve, one row each. The list above groups these by what is missing; this is the evidence behind it, and the sheet to reconcile against Zoho."
        sub={canExport
          ? "The export is the whole list, fetched from the server — never the page shown here."
          : "The sample the run itself carried. The full list, and its export, are open to managers and owners."}
        actions={
          <>
            {/* Only once the rows are in. While the fetch is out `held` is 0,
                and a chip reading "0 OF 1,304" would be this panel's own
                mistake in miniature — a count stated before it is known. The
                grid holds a skeleton for that moment; the chip holds nothing. */}
            {rows !== null && (
              <StatusChip
                label={complete
                  ? `ALL ${count(total)}`
                  : `${count(held)} OF ${count(total)}`}
                tone={complete ? "good" : "warn"}
                tip={complete
                  ? "Every row this pull skipped is in the grid below."
                  : "The grid below holds part of the list. Reconciling from what is on screen would leave the rest of it unaccounted for."}
              />
            )}
            {canExport && (
              <Button variant="outlined" size="small" onClick={exportCsv}
                      disabled={saving || held === 0}>
                {saving ? "Preparing…" : "Export to CSV"}
              </Button>
            )}
          </>
        }
      />

      {incomplete && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          <AlertTitle>This is not the whole list</AlertTitle>
          {incomplete}
        </Alert>
      )}
      {loadError && (
        <Alert severity="error" sx={{ mb: 2 }}>
          <AlertTitle>The full list did not load</AlertTitle>
          {/* The server's sentence stands on its own line: it may or may not
              end in a full stop, and running our prose onto the end of it made
              one sentence out of two whichever way it landed. */}
          {loadError}
          <Typography variant="body2" sx={{ mt: 1 }}>
            What follows is the smaller sample the run row itself carried, which
            is why the count beside the heading is short of the total.
          </Typography>
        </Alert>
      )}
      {exportError && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setExportError(null)}>
          <AlertTitle>The export did not download</AlertTitle>
          {exportError}
          <Typography variant="body2" sx={{ mt: 1 }}>
            The rows below are unaffected.
          </Typography>
        </Alert>
      )}

      <Bp sx={{ p: 0.25 }}>
        <DataGrid<SkippedRow>
          ariaLabel="Skipped rows"
          pageSize={25}
          twoLineRows
          rows={rows}
          getRowId={(r) => r.skip_id}
          columns={COLUMNS}
          renderNarrow={(r) => (
            // A `Paper`, not a `Card`: a skipped row is evidence behind the
            // worklist above, not a record with an identity somebody opens or
            // acts on — which is the line ui-standards §2 draws. `DataGrid`
            // keys these itself, so the card does not.
            <Bp sx={{ p: 1.5 }}>
              <b>{r.label || r.sku || r.ref}</b>
              <Meta>
                {r.document || r.ref}
                {r.document_date ? ` · ${r.document_date}` : ""}
              </Meta>
              <Meta>{r.code} — {r.detail}</Meta>
            </Bp>
          )}
        />
      </Bp>
    </>
  );
}
