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
//
// **Each file carries its own decoding config, and this is where it is checked
// and saved.** There is no default decoder: an upload is analysed on its own
// evidence — its headers, and every shipped rule set run over its first rows —
// and what comes back is a *proposal*. It decodes nothing until a person opens
// Decoding, reads the parser's counts and saves. So a file's state here is one
// of three, and only one of them builds: READY, NOT SAVED (a proposal nobody
// has confirmed) and NO RULE SET (columns saved, but no shipped rule set reads
// this manufacturer — which is a rule set somebody has to write, not a menu
// somebody has to find).

import { useRef, useState } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
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
import type { BuiltFile, CompanyCatalogueEntry, CompanySource } from "./types";
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
function rowsOf(source: CompanySource, built?: BuiltFile): string {
  const ingest = source.ingest;
  if (!ingest) return "not read yet";
  const kept = ingest.rows_kept ?? ingest.rows_read ?? 0;
  const skipped = ingest.rows_skipped_blank_key ?? 0;
  const emitted = built?.rows_emitted;
  const base = skipped > 0 ? `${kept} (${skipped} skipped)` : `${kept}`;
  return emitted !== undefined && emitted !== kept
    ? `${base} · ${emitted} used` : base;
}

/** One file's decoding state, in a word.
 *
 *  Three states rather than a boolean, because the fixes differ. NOT SAVED is
 *  a proposal nobody has confirmed — open it, check the counts, save. NO RULE
 *  SET is a config that is saved and still cannot run: no rule set this engine
 *  ships reads this manufacturer, which is a rule set somebody has to write.
 *  Collapsing the two into "not ready" would send a person looking through a
 *  menu for something that is not in it.
 */
function decodingChip(source: CompanySource): { label: string; tone: "good" | "warn" } {
  const d = source.decoding;
  if (!d.confirmed_at || !d.columns) return { label: "NOT SAVED", tone: "warn" };
  if (!d.rule_set || !d.rule_set_resolved) return { label: "NO RULE SET", tone: "warn" };
  return { label: "READY", tone: "good" };
}

/** The evidence a decoding config is chosen on: every shipped rule set over
 *  the first rows of this one file, with the parser's own counts.
 *
 *  Counts, never a rate and never an order. The numbers a score would be made
 *  of are the ones `catalog.run_parse` already refuses to recompute, and one
 *  "83% fit" would hide the distinction that decides the choice — a rule set
 *  that classifies few rows is wrong for this file, one that errors could not
 *  read it at all, and none of them reading it is the answer that means a rule
 *  set has to be written before this manufacturer can be decoded at all.
 *
 *  Quarantined is shown beside classified rather than subtracted from it,
 *  because "unknown means unknown" is the engine's contract: a quarantined row
 *  is kept, and a rule set that quarantines most of a file has told you
 *  something.
 */
function AnalysisEvidence({ source, busy, onAnalyze }: {
  source: CompanySource;
  busy: boolean;
  onAnalyze: () => void;
}) {
  const analysis = source.decoding.analysis;
  return (
    <Box sx={{ mt: 2.5 }}>
      <Stack direction="row" spacing={1}
             sx={{ alignItems: "center", mb: 1, flexWrap: "wrap", rowGap: 1 }}>
        <b>
          {analysis
            ? `Each rule set over the first ${analysis.sample_rows} rows of this file`
            : "This file has not been analysed"}
        </b>
        <Box sx={{ flex: 1 }} />
        <Button size="small" disabled={busy} onClick={onAnalyze}>Re-analyse</Button>
      </Stack>
      {analysis?.reason && (
        <Alert severity="info" sx={{ mb: 1 }}>{analysis.reason}</Alert>
      )}
      {analysis && analysis.candidates.length > 0 && (
        // A fact panel, not a grid: the row count is the rule set vocabulary
        // the pinned engine ships — a property of the deployment, fixed — which
        // is the case ui-standards §3 keeps as a table.
        <table className="facttable" aria-label="Parse counts per rule set">
          <tbody>
            {analysis.candidates.map((c) => (
              <tr key={c.rule_set}>
                <td>
                  {c.rule_set}
                  {c.rule_set === analysis.proposed && (
                    <span className="fsrc" style={{ marginLeft: 6 }}>proposed</span>
                  )}
                </td>
                <td className="fv">
                  {c.error ? (
                    <span>could not read this file — {c.error}</span>
                  ) : (
                    <>
                      {c.classified ?? 0} classified, {c.quarantined ?? 0} quarantined
                      <span className="fsrc" style={{ marginLeft: 8 }}>
                        of {c.rows_read ?? 0} rows
                      </span>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="st-help" style={{ marginTop: 6 }}>
        The parser&apos;s own counts, on a sample of this file. Nothing here
        ranks them — which one is right is a reading of these numbers.
      </p>
    </Box>
  );
}

/** Check and save how ONE file is decoded: its columns, and the rule set that
 *  reads its descriptions.
 *
 *  This is the validate step of the flow, and the only thing that turns an
 *  analysis into a config. Every column option is a header this file actually
 *  has, so a config naming a column that does not exist cannot be composed
 *  here — the server refuses one anyway, since the file could have been
 *  replaced under a stale dialog, but a form that can only express valid
 *  answers is the better half of that pair.
 *
 *  The rule set may be left at none. That is the honest state for a file no
 *  shipped rule set reads: the columns go on record, the file stays undecoded,
 *  and the build names it rather than quietly decoding it through somebody
 *  else's grammars.
 */
function DecodingDialog({ source, ruleSets, busy, onClose, onSave, onAnalyze }: {
  source: CompanySource;
  ruleSets: { id: string; path: string }[];
  busy: boolean;
  onClose: () => void;
  onSave: (config: { record_id: string; description: string;
                     grade?: string | null; rule_set?: string | null }) => void;
  onAnalyze: () => void;
}) {
  const columns = source.ingest?.columns ?? [];
  const d = source.decoding;
  // The saved config first, then what the analysis suggested from the headers.
  // A suggestion is not a config — the file is undecoded until this is saved —
  // but it is the right thing for the form to open on.
  const settled = d.columns ?? source.ingest?.mapped ?? null;
  const [recordId, setRecordId] = useState(settled?.record_id ?? "");
  const [description, setDescription] = useState(settled?.description ?? "");
  const [grade, setGrade] = useState(settled?.grade ?? "");
  const [ruleSet, setRuleSet] = useState(d.rule_set ?? d.analysis?.proposed ?? "");

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
      <DialogTitle>How is {source.filename} decoded?</DialogTitle>
      <DialogContent>
        {columns.length === 0 ? (
          // A source stored before its columns were read — the seeded corpus,
          // or an upload from before this existed. Its headers are not on
          // record, so there is nothing truthful to offer as options here.
          // Re-analyse reads the stored bytes again and puts them on record.
          <DialogContentText>
            This file was stored before its columns were read, so they are not
            on record to choose from — and nothing is decoded through a
            default, so it will not build as it stands. <b>Re-analyse</b> reads
            the stored file again and puts its headers and the parser&apos;s
            counts on record; then its columns and rule set can be saved here.
          </DialogContentText>
        ) : (
        <>
        <DialogContentText sx={{ mb: 2 }}>
          These three columns are all that is read. Every other column in this
          file — price, cost, stock, anything else — is left out of the
          catalogue entirely. Nothing is inherited from the catalogue or the
          deployment: <b>this file is not decoded until this is saved</b>.
        </DialogContentText>
        <Stack spacing={2.5} sx={{ mt: 1 }}>
          {field("Part number", "The code a customer quotes back at you.",
                 recordId, setRecordId)}
          {field("Description",
                 "The text the rule set decodes — dimensions, geometry, grade.",
                 description, setDescription)}
          {field("Grade",
                 "Optional. Leave as none where the grade is inside the description.",
                 grade, setGrade, true)}
          <TextField select fullWidth size="small" label="Rule set" value={ruleSet}
                     disabled={busy} onChange={(e) => setRuleSet(e.target.value)}
                     helperText={
                       d.rule_set && !d.rule_set_resolved
                         ? `This engine no longer ships ${d.rule_set}, which this file names — it decodes nothing until another is chosen.`
                         : ruleSets.length === 0
                           ? "none available — this deployment ships no rule set"
                           : "The decoder for this manufacturer's descriptions, from what the engine ships. The counts below are what says whether it reads this file."}>
            <MenuItem value="">— none yet —</MenuItem>
            {ruleSets.map((r) => (
              <MenuItem key={r.id} value={r.id}>{r.id}</MenuItem>
            ))}
            {/* A rule set this file names and the pinned engine no longer has.
                Offered rather than dropped: silently blanking the menu would
                make a stored choice look like no choice at all. */}
            {d.rule_set && !ruleSets.some((r) => r.id === d.rule_set) && (
              <MenuItem value={d.rule_set}>
                {d.rule_set} — not shipped by this engine
              </MenuItem>
            )}
          </TextField>
        </Stack>
        </>
        )}
        <AnalysisEvidence source={source} busy={busy} onAnalyze={onAnalyze} />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>
          {columns.length === 0 ? "Close" : "Cancel"}
        </Button>
        {columns.length > 0 && (
        <Button variant="contained" disabled={busy || !recordId || !description}
                onClick={() => onSave({ record_id: recordId, description,
                                        grade: grade || null,
                                        rule_set: ruleSet || null })}>
          Save decoding config
        </Button>
        )}
      </DialogActions>
    </Dialog>
  );
}

export function CatalogSources({ catalogue, label, ruleSets, canManage, busy,
                                onUpload, onSaveDecoding, onAnalyze, onRemove }: {
  catalogue: CompanyCatalogueEntry;
  /** The company's label, for the grid's name: a company keeps one of these
   *  per manufacturer, so the catalogue's own name alone does not say whose
   *  files a screen reader is describing. */
  label: string;
  /** The rule sets this engine ships, which a file's config may name. */
  ruleSets: { id: string; path: string }[];
  canManage: boolean;
  busy: boolean;
  /** A key replaces that one file; no key replaces the whole export. */
  onUpload: (file: File, sourceKey?: string) => void;
  onSaveDecoding: (sourceKey: string,
                   config: { record_id: string; description: string;
                             grade?: string | null;
                             rule_set?: string | null }) => void;
  onAnalyze: (sourceKey: string) => void;
  onRemove: (sourceKey: string) => void;
}) {
  /** Which file's decoding is open, by key rather than by value: re-analysing
   *  replaces the whole company, so a dialog holding the row it was opened
   *  with would go on showing the counts from before it ran. */
  const [decodingKey, setDecodingKey] = useState<string | null>(null);
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
  const built = new Map<string, BuiltFile>(
    (catalogue.ingest?.sources ?? []).map((s) => [s.source_key, s]));
  const decoding = catalogue.sources.find((s) => s.source_key === decodingKey) ?? null;

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
      headerName: "Decoding", minWidth: 130, flex: 1,
      // The one state that decides whether this file builds. A chip rather
      // than coloured text, per ui-standards §6.
      cellRenderer: (p: { data: CompanySource }) => {
        const chip = decodingChip(p.data);
        return <StatusChip label={chip.label} tone={chip.tone}
                           tip={p.data.decoding.rule_set
                             ? `Decoded through ${p.data.decoding.rule_set}.`
                             : "No rule set decodes this file yet, so a build will name it rather than decode it."} />;
      },
      valueGetter: (p: { data?: CompanySource }) =>
        p.data ? decodingChip(p.data).label : "",
    },
    {
      headerName: "Reads", flex: 2, minWidth: 190,
      valueGetter: (p: { data?: CompanySource }) => {
        const m = p.data?.decoding.columns ?? p.data?.ingest?.mapped;
        if (!m) return "not chosen yet";
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
                  onClick={() => setDecodingKey(p.data.source_key)}>Decoding</Button>
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
            <div>
              <StatusChip label={decodingChip(r).label} tone={decodingChip(r).tone} />
            </div>
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
                        onClick={() => setDecodingKey(r.source_key)}>Decoding</Button>
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

      {decoding && (
        <DecodingDialog
          // Remounted when a re-analysis changes what is proposed, so the form
          // offers the fresh proposal rather than the one it opened with.
          key={`${decoding.source_key}:${decoding.decoding.analysis?.proposed ?? ""}`}
          source={decoding} ruleSets={ruleSets} busy={busy}
          onClose={() => setDecodingKey(null)}
          onAnalyze={() => onAnalyze(decoding.source_key)}
          onSave={(config) => {
            onSaveDecoding(decoding.source_key, config);
            setDecodingKey(null);
          }} />
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
