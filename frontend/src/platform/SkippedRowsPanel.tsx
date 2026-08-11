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

import { useCallback, useEffect, useState } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import { money } from "../money";
import { papi } from "./api";
import { DataGrid, numeric, type ColDef } from "./DataGrid";
import { saveBlob } from "./download";
import { Bp, Labelled } from "./ui";
import type { SkippedRow, SyncRun } from "./types";

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
        <div className="fsrc">
          {p.data.document_date || "—"}
          {p.data.party ? ` · ${p.data.party}` : ""}
        </div>
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
        <div className="fsrc">
          {p.data.sku ? `SKU ${p.data.sku} · ` : ""}
          {p.data.missing_id ? `id ${p.data.missing_id}` : p.data.ref}
        </div>
      </div>
    ) : null,
  },
  {
    headerName: "Why", flex: 1, minWidth: 190, filter: "agTextColumnFilter",
    valueGetter: (p) => p.data?.code || "",
    cellRenderer: (p: { data?: SkippedRow }) => p.data ? (
      <div>
        <b>{p.data.code}</b>
        <div className="fsrc">{p.data.detail}</div>
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
  const [error, setError] = useState<string | null>(null);
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
      setError((e as Error).message);
    });
    return () => { live = false; };
  }, [token, runId, canExport, run]);

  const exportCsv = useCallback(async () => {
    setSaving(true);
    setError(null);
    try {
      const { blob, filename } = await papi.syncSkippedCsv(token, runId);
      saveBlob(blob, filename);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }, [token, runId]);

  const held = rows?.length ?? 0;
  // Said plainly, because it is the number the whole panel is about. "Showing
  // all 1,304" and "showing 20 of 1,304" must never look the same.
  const coverage = !canExport
    ? `showing the first ${held} of ${run.skipped_count}`
    : held === run.skipped_count
      ? `all ${run.skipped_count}`
      : `${held} of ${run.skipped_count}`;

  return (
    <>
      <div className="section-h">
        <Labelled tip="Every document line this pull could not fully resolve, one row each. The list above groups these by what is missing; this is the evidence behind it, and the sheet to reconcile against Zoho.">
          Skipped rows
        </Labelled>
        <span className="fsrc"> {coverage}</span>
      </div>

      {incomplete && (
        <Box sx={{ mb: 1 }}>
          <Alert severity="warning">{incomplete}</Alert>
        </Box>
      )}
      {error && (
        <Box sx={{ mb: 1 }}>
          <Alert severity="error">{error}</Alert>
        </Box>
      )}

      {canExport && (
        <Stack direction="row" spacing={1} sx={{ mb: 1, alignItems: "center" }}>
          <Button variant="outlined" size="small" onClick={exportCsv}
                  disabled={saving || held === 0}>
            {saving ? "Preparing…" : "Export to CSV"}
          </Button>
          <Typography variant="body2" color="text.secondary">
            The whole list, not the page shown here.
          </Typography>
        </Stack>
      )}

      <Bp style={{ padding: 2 }}>
        <DataGrid<SkippedRow>
          ariaLabel="Skipped rows"
          pageSize={25}
          twoLineRows
          rows={rows}
          getRowId={(r) => r.skip_id}
          columns={COLUMNS}
          renderNarrow={(r) => (
            <Bp key={r.skip_id} style={{ padding: "10px 12px" }}>
              <b>{r.label || r.sku || r.ref}</b>
              <div className="fsrc">{r.document || r.ref}
                {r.document_date ? ` · ${r.document_date}` : ""}</div>
              <div className="fsrc">{r.code} — {r.detail}</div>
            </Bp>
          )}
        />
      </Bp>
    </>
  );
}
