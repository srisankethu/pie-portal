// The files one catalogue is decoded from, and how each one is read.
//
// A company used to have exactly one item-master export, and replacing it was
// the only thing that could be done to it. That is not how the exports arrive:
// there is an item master out of the ERP, a manufacturer's range extension for
// products the master has not caught up with, and a price list covering a line
// bought this quarter. Building from one of them means the other two are not in
// the catalogue, and a part number that is not in the catalogue resolves to
// UNKNOWN — so the answer was to keep the set and merge it at build time.
//
// **This is a grid, not a fact panel.** The row count is the number of exports
// a business keeps, which grows with the business rather than with the shape of
// the screen — `docs/ui-standards.md` §3's test, and the reason the Quote
// Builder's line table should not have stayed a `<table>` for three UI passes.
//
// Two columns here exist to make one invariant checkable rather than merely
// asserted. "Reads" says which of the file's own columns became the part number
// and the description; "Ignored" names what did not, commercial columns first.
// The catalogue is nomenclature only — a price is absent from it because it was
// never written, not because a filter removed it — and a person can only
// confirm that against their own spreadsheet if the columns are named.

import { useRef, useState } from "react";
import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogContentText from "@mui/material/DialogContentText";
import DialogTitle from "@mui/material/DialogTitle";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";

import type { ColDef } from "./DataGrid";
import { DataGrid } from "./DataGrid";
import { EmptyState, StatusChip } from "./kit";
import type { CompanyCatalogueEntry, CompanySource, SourceIngest } from "./types";
import { Tip } from "./ui";
import { formatDateTime } from "../when";

/** A file's size, in the unit that makes it legible.
 *
 *  Always-megabytes rendered every real file as "0.0 MB" — a 700-byte test
 *  export, a 200 kB range extension, the seeded corpus. That reads as an upload
 *  that did not work, on the one screen whose job is to say what was uploaded.
 *  The ceiling is still quoted in MB, because that is the unit the limit is set
 *  in.
 */
export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} kB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** The upload ceiling, which is set and talked about in megabytes. */
export function megabytes(bytes: number): string {
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** What this file contributes, in one phrase.
 *
 *  Rows *kept* rather than rows read: a file whose part-number column is empty
 *  for half its rows contributes half of them, and reporting the larger number
 *  would describe a catalogue nobody is going to get. Where the two differ the
 *  gap is shown, because it is usually the wrong column mapped rather than a
 *  genuinely sparse file.
 *
 *  `built` is the same file's line in the last build's merge report, and it is
 *  shown only when it disagrees: a file every row of which a newer file already
 *  carried contributes **nothing**, which is worth seeing on the screen where
 *  somebody decides whether to keep it.
 */
function rowsOf(source: CompanySource, built?: SourceIngest): string {
  const ingest = source.ingest;
  if (!ingest) return "not read yet";
  const kept = ingest.rows_kept ?? ingest.rows_read ?? 0;
  const skipped = ingest.rows_skipped_blank_key ?? 0;
  const emitted = built?.rows_emitted;
  const base = skipped > 0 ? `${kept} (${skipped} skipped)` : `${kept}`;
  return emitted !== undefined && emitted !== kept
    ? `${base} · ${emitted} used` : base;
}

/** Choose which columns of one file the parse reads.
 *
 *  Every option is a header this file actually has, so a mapping naming a
 *  column that does not exist cannot be composed here — the server refuses one
 *  anyway, since the file could have been replaced under a stale dialog, but a
 *  form that can only express valid answers is the better half of that pair.
 */
function MappingDialog({ source, onClose, onSave, busy }: {
  source: CompanySource;
  onClose: () => void;
  onSave: (mapping: { record_id: string; description: string;
                      grade?: string | null }) => void;
  busy: boolean;
}) {
  const columns = source.ingest?.columns ?? [];
  const mapped = source.mapping ?? source.ingest?.mapped ?? null;
  const [recordId, setRecordId] = useState(mapped?.record_id ?? "");
  const [description, setDescription] = useState(mapped?.description ?? "");
  const [grade, setGrade] = useState(mapped?.grade ?? "");

  const field = (label: string, tip: string, value: string,
                 set: (v: string) => void, optional = false) => (
    <TextField select fullWidth size="small" label={label} value={value}
               helperText={tip} disabled={busy}
               onChange={(e) => set(e.target.value)}>
      {optional && <MenuItem value="">— none —</MenuItem>}
      {columns.map((c) => <MenuItem key={c} value={c}>{c}</MenuItem>)}
    </TextField>
  );

  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>Which columns of {source.filename}?</DialogTitle>
      <DialogContent>
        {columns.length === 0 ? (
          // A source stored before columns were read — the seeded corpus, or an
          // upload from before this existed. Its headers are not on record, so
          // there is nothing truthful to offer as options here. It still builds
          // (the headers are read at build time and mapped by their names), and
          // re-uploading it under Replace is what puts its columns on record.
          <DialogContentText>
            This file was stored before its columns were read, so they are not
            on record to choose from. It still decodes — its headers are read
            at build time — but to map them by hand, use <b>Replace</b> on this
            row and upload the same file again.
          </DialogContentText>
        ) : (
        <>
        <DialogContentText sx={{ mb: 2 }}>
          These three are all that is read. Every other column in this file —
          price, cost, stock, anything else — is left out of the catalogue
          entirely.
        </DialogContentText>
        <Stack spacing={2.5} sx={{ mt: 1 }}>
          {field("Part number", "The code a customer quotes back at you.",
                 recordId, setRecordId)}
          {field("Description",
                 "The text the pack decodes — dimensions, geometry, grade.",
                 description, setDescription)}
          {field("Grade",
                 "Optional. Leave as none where the grade is inside the description.",
                 grade, setGrade, true)}
        </Stack>
        </>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>
          {columns.length === 0 ? "Close" : "Cancel"}
        </Button>
        {columns.length > 0 && (
        <Button variant="contained" disabled={busy || !recordId || !description}
                onClick={() => onSave({ record_id: recordId,
                                        description, grade: grade || null })}>
          Save and re-read
        </Button>
        )}
      </DialogActions>
    </Dialog>
  );
}

export function CatalogSources({ catalogue, label, canManage, busy, onUpload,
                                onMap, onRemove }: {
  catalogue: CompanyCatalogueEntry;
  /** The company's label, for the grid's name: a company keeps one of these
   *  per manufacturer, so the catalogue's own name alone does not say whose
   *  files a screen reader is describing. */
  label: string;
  canManage: boolean;
  busy: boolean;
  /** A key replaces that one file; no key replaces the whole export. */
  onUpload: (file: File, sourceKey?: string) => void;
  onMap: (sourceKey: string, mapping: { record_id: string; description: string;
                                        grade?: string | null }) => void;
  onRemove: (sourceKey: string) => void;
}) {
  const [mapping, setMapping] = useState<CompanySource | null>(null);
  const [confirmRemove, setConfirmRemove] = useState<CompanySource | null>(null);
  // One hidden input per action, because a file input that is reused for "add"
  // and "replace this one" needs a mode flag read inside its own change
  // handler — which is a second piece of state that can disagree with the
  // button that was pressed.
  const addInput = useRef<HTMLInputElement | null>(null);
  const replaceInput = useRef<HTMLInputElement | null>(null);
  const replacing = useRef<string | null>(null);

  // What the last build made of each file, keyed the way the rows are. Absent
  // for a catalogue that has not built yet, and for a file added since.
  const built = new Map<string, SourceIngest>(
    (catalogue.ingest?.sources ?? []).map((s) => [s.source_key ?? "", s]));

  const columns: ColDef<CompanySource>[] = [
    {
      field: "filename", headerName: "File", flex: 2, minWidth: 180,
      cellRenderer: (p: { data: CompanySource }) => (
        <span>
          {p.data.filename}
          <span className="fsrc" style={{ marginLeft: 8 }}>
            {fileSize(p.data.size_bytes)}
          </span>
        </span>
      ),
    },
    {
      headerName: "Rows", minWidth: 110, flex: 1,
      valueGetter: (p: { data?: CompanySource }) =>
        p.data ? rowsOf(p.data, built.get(p.data.source_key)) : "",
    },
    {
      headerName: "Reads", flex: 2, minWidth: 190,
      valueGetter: (p: { data?: CompanySource }) => {
        const m = p.data?.mapping ?? p.data?.ingest?.mapped;
        if (!m) return "headers read at build time";
        return [m.record_id, m.description].filter(Boolean).join(" · ");
      },
    },
    {
      headerName: "Ignored", flex: 2, minWidth: 190,
      cellRenderer: (p: { data: CompanySource }) => {
        const ingest = p.data.ingest;
        if (!ingest) return <span className="fsrc">—</span>;
        const money = ingest.commercial_columns_dropped;
        const total = ingest.dropped_columns.length;
        if (total === 0) return <span className="fsrc">nothing</span>;
        return (
          <Tooltip title={ingest.dropped_columns.join(", ")}>
            <span>
              {total} column{total === 1 ? "" : "s"}
              {money.length > 0 && (
                <span className="fsrc" style={{ marginLeft: 6 }}>
                  incl. {money.slice(0, 2).join(", ")}
                  {money.length > 2 ? " …" : ""}
                </span>
              )}
            </span>
          </Tooltip>
        );
      },
    },
    {
      headerName: "Uploaded", flex: 1.4, minWidth: 150,
      valueGetter: (p: { data?: CompanySource }) =>
        p.data ? formatDateTime(p.data.uploaded_at) : "",
    },
    ...(canManage ? [{
      headerName: "", minWidth: 210, sortable: false, filter: false,
      // The row's own controls, so clicking them is not also a row click.
      cellRenderer: (p: { data: CompanySource }) => (
        <Stack direction="row" spacing={0.5}>
          <Button size="small" disabled={busy}
                  onClick={() => setMapping(p.data)}>Columns</Button>
          <Button size="small" disabled={busy}
                  onClick={() => {
                    replacing.current = p.data.source_key;
                    replaceInput.current?.click();
                  }}>Replace</Button>
          <Button size="small" color="error" disabled={busy}
                  onClick={() => setConfirmRemove(p.data)}>Remove</Button>
        </Stack>
      ),
    } as ColDef<CompanySource>] : []),
  ];

  return (
    <div>
      <Stack direction="row" spacing={1} sx={{ mb: 1, alignItems: "center", flexWrap: "wrap", rowGap: 1 }}>
        <b>Files this catalogue is built from</b>
        <Tip text="Every file here is merged into one catalogue. Where the same part number appears in two of them, the newest file's row is used and the overlap is counted — it is never quietly dropped, because two exports disagreeing about one product is something somebody has to know about." />
        <div style={{ flex: 1 }} />
        {catalogue.sources.length > 1 && (
          <StatusChip label={`${catalogue.sources.length} FILES`} tone="neutral" />
        )}
        {canManage && (
          <Button size="small" variant="outlined" disabled={busy}
                  onClick={() => addInput.current?.click()}>
            Add a file
          </Button>
        )}
      </Stack>

      <input ref={addInput} type="file" hidden
             accept=".csv,.xlsx,.xlsm,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
             onChange={(e) => {
               const file = e.target.files?.[0];
               e.target.value = "";     // so the same file can be re-picked
               // Keyed by its own name: uploading `prices.xlsx` twice replaces
               // that file rather than merging two copies of it.
               if (file) onUpload(file, file.name);
             }} />
      <input ref={replaceInput} type="file" hidden
             accept=".csv,.xlsx,.xlsm,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
             onChange={(e) => {
               const file = e.target.files?.[0];
               const key = replacing.current;
               e.target.value = "";
               replacing.current = null;
               if (file && key) onUpload(file, key);
             }} />

      <DataGrid<CompanySource>
        rows={catalogue.sources}
        columns={columns}
        getRowId={(r) => r.source_key}
        pageSize={10}
        filters={false}
        ariaLabel={`Files ${label || catalogue.connection_id} · ${catalogue.name || catalogue.catalogue_key} decodes`}
        empty={<EmptyState
          title="No export uploaded"
          reason="Item identity lookups and this company's RFQ line resolution answer UNKNOWN — not zero coverage — until a file is uploaded and decoded. A CSV or an Excel export of the item master is what this reads; price and stock columns in it are ignored."
        />}
        renderNarrow={(r) => (
          <Stack spacing={0.5} sx={{ p: 1.5 }}>
            <b>{r.filename}</b>
            <span className="fsrc">
              {rowsOf(r, built.get(r.source_key))} rows ·{" "}
              {fileSize(r.size_bytes)} ·{" "}
              {formatDateTime(r.uploaded_at)}
            </span>
            {/* Named on a phone too. The nomenclature-only claim is checkable
                only against the columns' own names, and a narrow screen is not
                a reason to make a person take it on trust. */}
            {r.ingest && r.ingest.dropped_columns.length > 0 && (
              <span className="fsrc">
                ignored: {r.ingest.dropped_columns.slice(0, 4).join(", ")}
                {r.ingest.dropped_columns.length > 4 ? " …" : ""}
              </span>
            )}
            {canManage && (
              <Stack direction="row" spacing={0.5}>
                <Button size="small" disabled={busy}
                        onClick={() => setMapping(r)}>Columns</Button>
                <Button size="small" color="error" disabled={busy}
                        onClick={() => setConfirmRemove(r)}>Remove</Button>
              </Stack>
            )}
          </Stack>
        )}
      />

      {catalogue.ingest && catalogue.ingest.collisions > 0 && (
        <Alert severity="info" sx={{ mt: 1 }}>
          {catalogue.ingest.collisions} part number
          {catalogue.ingest.collisions === 1 ? "" : "s"} appeared in more than one
          file at the last build — the newest file&apos;s row was used for each.
          {catalogue.ingest.collision_examples.length > 0 &&
            ` For example ${catalogue.ingest.collision_examples.slice(0, 5).join(", ")}.`}
        </Alert>
      )}

      {mapping && (
        <MappingDialog
          source={mapping} busy={busy}
          onClose={() => setMapping(null)}
          onSave={(m) => { onMap(mapping.source_key, m); setMapping(null); }} />
      )}

      <Dialog open={confirmRemove !== null} onClose={() => setConfirmRemove(null)}>
        <DialogTitle>Stop building from {confirmRemove?.filename}?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            The catalogue on disk is left exactly as it is and keeps resolving —
            it becomes out of date, and the next rebuild is what drops these
            rows. The file itself is kept, so a catalogue already built keeps a
            real record of what it was built from.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmRemove(null)}>Cancel</Button>
          <Button color="error" variant="contained" onClick={() => {
            if (confirmRemove) onRemove(confirmRemove.source_key);
            setConfirmRemove(null);
          }}>Remove</Button>
        </DialogActions>
      </Dialog>
    </div>
  );
}
