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
// evidence and what comes back is a *proposal*. It decodes nothing until a
// person opens Decoding and saves. So a file's state here is one of three, and
// only one of them builds: READY, NOT SAVED (a proposal nobody has confirmed)
// and NO DECODER (columns saved, and still nothing that reads the
// descriptions).
//
// **Two ways a file can be decoded, and a config names exactly one.** Either a
// rule set pie-parser ships — fifty attributes richly bound and a grade
// grammar behind them, for the manufacturers it was written for — or a
// **decoder built from this file's own descriptions**, which needs no shipped
// grammar and starts knowing only what the file says. The dialog offers them
// as a toggle rather than as two sections, because the server refuses a config
// naming both: a form that can express only one answer is the honest shape of
// a rule that allows only one.
//
// The decoder half is a review, and the review is a grid. Every varying part
// of every shape comes back with what the file actually put in it and the
// attribute it reads as — 330 of them on the shipped corpus, of which the
// file's own text names twelve. Slot and type are editable; the patterns are
// not, because a binding names a dimension and this review exists to catch a
// bad one, while a pattern is a regular expression that would run over every
// row of every rebuild.

import { useMemo, useRef, useState } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogContentText from "@mui/material/DialogContentText";
import DialogTitle from "@mui/material/DialogTitle";
import LinearProgress from "@mui/material/LinearProgress";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import type { ColDef } from "./DataGrid";
import { DataGrid } from "./DataGrid";
import { EmptyState, StatusChip } from "./kit";
import type { BindingChoice, BindingSuggestion, BuiltFile, CompanyCatalogueEntry,
              CompanySource, DecoderArtifact, DecoderProposalResponse } from "./types";
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
  if (!d.path) return { label: "NO DECODER", tone: "warn" };
  return { label: "READY", tone: "good" };
}

/** Which of the two paths decodes this file, for the row that lists it.
 *
 *  Named rather than left to the READY chip, because the two are not
 *  interchangeable: one is a grammar pie-parser ships for a manufacturer, the
 *  other was built from this file's own descriptions and knows only what the
 *  file says. Which one read a row is the first thing anybody asks when a
 *  decoded value looks wrong. */
function decodedBy(source: CompanySource): string {
  const d = source.decoding;
  if (d.path === "rule_set") return d.rule_set ?? "";
  if (d.path === "decoder") return `decoder ${d.decoder_id ?? ""}`.trim();
  return "—";
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

/** One row of the binding review: a capture group, its evidence, and the slot
 *  a person is confirming for it. */
interface ReviewRow {
  id: string;
  segment: string;
  group: string;
  kind: string;
  occurrences: number;
  rowsMatched: number;
  samples: string;
  around: string;
  slot: string;
  type: string;
  source: string;
  reason: string;
  detail: string;
  candidates: string[];
  types: string[];
}

/** Which types a group's own values would survive, narrowest first.
 *
 *  Mirrors `decoding.bind.types_for` rather than offering all four: a group
 *  holding `5.1` typed as an integer is a thousand rows quarantined at decode
 *  time, and the server refuses it — a form that can only express valid
 *  answers is the better half of that pair, exactly as the column pickers
 *  above only offer headers the file has. */
function typesFor(row: BindingSuggestion): string[] {
  const e = row.evidence;
  const out: string[] = [];
  if (e?.all_integer) out.push("integer");
  if (e?.all_numeric) out.push("number");
  out.push("text");
  if (e?.kind === "optional") out.push("flag");
  return out;
}

/** One row's confirmed answer, as the dialog holds it while it is open. */
export interface BindingEdit { slot: string; type: string }

/** The review's edits with one row's answer applied.
 *
 *  Exported and pure because it is the only logic in this dialog worth
 *  testing on its own: driving ag-grid's cell editor through the DOM to reach
 *  it would test the grid, and a test that cannot reach a rule is how the rule
 *  below got written twice.
 *
 *  **Choosing a slot with no type yet defaults the type**, to the narrowest
 *  the group's own values support. A binding needs both and nothing on the
 *  screen says so, so without this, picking an attribute on a row nobody had
 *  named saved *nothing* — the row looked answered and the decoder did not
 *  read it.
 *
 *  Clearing the slot clears the type with it. A type without a slot is not a
 *  binding, and leaving one behind would make the next slot chosen inherit a
 *  type from an attribute it has nothing to do with.
 */
export function nextEdits(prev: Record<string, BindingEdit>, id: string,
                          patch: Partial<BindingEdit>,
                          types: string[]): Record<string, BindingEdit> {
  const was = prev[id];
  const slot = patch.slot ?? was?.slot ?? "";
  if (!slot) return { ...prev, [id]: { slot: "", type: "" } };
  return {
    ...prev,
    [id]: { slot, type: patch.type ?? was?.type ?? types[0] ?? "text" },
  };
}

/** The types one row of the review may take, found by its id.
 *
 *  The dialog holds the edits and the panel holds the rows, so the answer has
 *  to be reachable from the id alone — the alternative is passing the row up
 *  through the edit callback, which would make the callback's shape depend on
 *  the grid's. */
function typesForGroup(proposal: DecoderProposalResponse | null,
                       id: string): string[] {
  const found = (proposal?.review?.suggestions ?? [])
    .find((sg) => `${sg.segment}/${sg.group}` === id);
  return found ? typesFor(found) : [];
}

/** Why a group has no suggestion, in a sentence rather than a code.
 *
 *  The codes are stable and the screen is not the place to learn them. Two of
 *  them mean *nothing may be bound here at all* — no evidence, or two
 *  different words one binding cannot express — and those read differently
 *  from "nobody has decided yet", which is a question waiting for the person
 *  reading it. */
const DECLINE_REASON: Record<string, string> = {
  NO_OCCURRENCES: "No row uses this part, so there is nothing to bind it on.",
  NO_UNIT: "The file writes no unit here, so its text does not say what this is.",
  AMBIGUOUS_UNIT: "The unit narrows it, but not to one slot — choose which.",
  MIXED_VALUES: "This part holds two different words, which one binding cannot express. Splitting it is inference's job.",
  UNKNOWN_WORD: "One word, and not one the vocabulary knows.",
  SLOT_TAKEN: "Another group of this segment already reads that slot.",
  FROM_UNIT: "Named from the unit written next to it in the file.",
  FROM_WORD: "Named from the word this part contains.",
  FROM_MODEL: "Named by a model, from the evidence — check it.",
};

/** The binding review: every capture group of a proposed decoder, what the
 *  file actually put in it, and the slot it fills.
 *
 *  **A grid, not a fact panel** (`docs/ui-standards.md` §3). The row count is
 *  the number of varying parts in one manufacturer's descriptions — 330 on the
 *  shipped corpus — which is set by the file rather than by the shape of the
 *  screen. That is the test, and this is the case the rule exists for.
 *
 *  Every group appears, named or not. A review listing only the answered ones
 *  is a review a person can finish without ever seeing what nobody decided
 *  about — and on a real file that is 318 of the 330.
 *
 *  Slot and type are editable cells; the options offered are what that
 *  group's own evidence allows, so a wrong answer here is a wrong *attribute*
 *  rather than a decoder that cannot build. The patterns are not editable at
 *  all: a binding names a dimension and this review exists to catch a bad one,
 *  but a pattern is a regular expression that runs over every row of every
 *  rebuild. */
function BindingReviewGrid({ suggestions, edits, onEdit }: {
  suggestions: BindingSuggestion[];
  edits: Record<string, BindingEdit>;
  onEdit: (id: string, patch: Partial<BindingEdit>) => void;
}) {
  const rows = useMemo<ReviewRow[]>(() => suggestions.map((sg) => {
    const id = `${sg.segment}/${sg.group}`;
    const edit = edits[id];
    const e = sg.evidence ?? null;
    const around = [e?.left ? `…${e.left}` : "", e?.right ? `${e.right}…` : ""]
      .filter(Boolean).join("  ▸  ");
    return {
      id,
      segment: sg.segment,
      group: sg.group,
      kind: e?.kind ?? "",
      occurrences: e?.occurrences ?? 0,
      rowsMatched: e?.rows_matched ?? 0,
      samples: (e?.samples ?? []).join(", "),
      around,
      slot: edit?.slot ?? sg.slot ?? "",
      type: edit?.type ?? sg.type ?? "",
      source: sg.source,
      reason: sg.reason,
      detail: sg.detail ?? "",
      candidates: sg.candidates,
      types: typesFor(sg),
    };
  // `edits` is read here on purpose: an edited row must re-render with the
  // person's answer rather than the proposal's.
  }), [suggestions, edits]);

  const columns: ColDef<ReviewRow>[] = [
    { field: "segment", headerName: "Shape", minWidth: 150, flex: 1 },
    { field: "group", headerName: "Part", width: 90 },
    {
      field: "samples", headerName: "What it holds", minWidth: 220, flex: 2,
      tooltipValueGetter: (p) => (p.data?.samples ?? ""),
    },
    {
      field: "around", headerName: "Written around it", minWidth: 150, flex: 1,
      // The unit is the whole basis for a name the file itself settles, so it
      // is a column rather than a detail behind a click.
    },
    {
      field: "occurrences", headerName: "In rows", width: 110,
      type: "numericColumn",
      valueFormatter: (p) => `${p.value} / ${p.data?.rowsMatched ?? 0}`,
    },
    {
      field: "slot", headerName: "Reads as", minWidth: 200, flex: 1,
      editable: (p) => (p.data?.candidates.length ?? 0) > 0,
      cellEditor: "agSelectCellEditor",
      cellEditorParams: (p: { data?: ReviewRow }) =>
        ({ values: ["", ...(p.data?.candidates ?? [])] }),
      valueFormatter: (p) => p.value || "— not bound —",
    },
    {
      field: "type", headerName: "As a", width: 130,
      editable: (p) => Boolean(p.data?.slot),
      cellEditor: "agSelectCellEditor",
      cellEditorParams: (p: { data?: ReviewRow }) =>
        ({ values: p.data?.types ?? [] }),
    },
    {
      field: "reason", headerName: "Why", minWidth: 240, flex: 2,
      valueFormatter: (p) =>
        (DECLINE_REASON[p.value as string] ?? p.value)
        + (p.data?.detail ? ` (${p.data.detail})` : ""),
      tooltipValueGetter: (p) =>
        DECLINE_REASON[p.data?.reason ?? ""] ?? p.data?.reason ?? "",
    },
  ];

  return (
    <DataGrid<ReviewRow>
      rows={rows} columns={columns} getRowId={(r) => r.id} pageSize={25}
      // A fixed height because this sits inside a dialog: sized to its rows,
      // three hundred and thirty of them would push the save button off the
      // screen, which is the one control the review exists to reach.
      height={420}
      onCellValueChanged={(row, field, value) =>
        onEdit(row.id, { [field]: String(value ?? "") })}
      ariaLabel="Capture groups of the proposed decoder, and the attribute each reads as"
      empty={<EmptyState title="No parts to review"
                         reason="This decoder captures nothing that varies, so there is nothing to name." />}
    />
  );
}

/** Propose a decoder from this file, review what it captured, and save it.
 *
 *  The other half of "how is this file decoded", and the half that needs no
 *  shipped grammar. Nothing is proposed until somebody asks — inference reads
 *  the whole file and, where a live provider is configured, spends a call per
 *  batch of groups the text could not name.
 *
 *  Three numbers carry the judgement and none of them is a score: how many of
 *  the file's rows a segment claimed, how many groups the file's own text
 *  named, and how many are still open. Whether that is good enough is the
 *  reviewer's call, which is why there is no "83% fit" here — the same reason
 *  the rule-set evidence above shows counts rather than a rate. */
function DecoderPanel({ source, busy, proposal, proposing, error, edits,
                       decimal, onPropose, onEdit, onDecimal }: {
  source: CompanySource;
  busy: boolean;
  proposal: DecoderProposalResponse | null;
  proposing: boolean;
  error: string | null;
  edits: Record<string, BindingEdit>;
  decimal: string;
  onPropose: () => void;
  onEdit: (id: string, patch: Partial<BindingEdit>) => void;
  onDecimal: (v: string) => void;
}) {
  const saved = source.decoding.decoder;
  const p = proposal?.proposal;
  const review = proposal?.review;

  return (
    <Box sx={{ mt: 2.5 }}>
      <Stack direction="row" spacing={2}
             sx={{ mb: 1.5, alignItems: "center",
                   justifyContent: "space-between", flexWrap: "wrap",
                   rowGap: 1 }}>
        <Box>
          <Typography variant="subtitle2">A decoder built from this file</Typography>
          <Typography variant="caption" color="text.secondary">
            {saved
              ? `Saved: ${saved.decoder_id}, ${saved.segments.length} shape${saved.segments.length === 1 ? "" : "s"}, reading ${saved.decimal === "either" ? "either decimal separator" : `“${saved.decimal === "dot" ? "." : ","}” as the decimal point`}.`
              : "Nothing shipped reads this file. Inference reads its descriptions, works out the shapes in them, and proposes a decoder for this file alone."}
          </Typography>
        </Box>
        <Button size="small" variant={saved ? "outlined" : "contained"}
                disabled={busy || proposing} onClick={onPropose}>
          {proposal ? "Propose again" : "Propose a decoder"}
        </Button>
      </Stack>

      {proposing && <LinearProgress sx={{ mb: 1.5 }} />}
      {error && <Alert severity="error" sx={{ mb: 1.5 }}>{error}</Alert>}

      {p && !p.decoder && (
        <Alert severity="warning">
          {p.reason ?? "No decoder could be proposed from this file."}
        </Alert>
      )}

      {p?.decoder && review && (
        <>
          <Alert severity="info" sx={{ mb: 1.5 }}>
            {p.claimed} of {p.rows_read} rows fall into{" "}
            {p.decoder.segments.length} shape
            {p.decoder.segments.length === 1 ? "" : "s"}; {p.unclaimed} match
            none and would be kept unread. Of {review.suggestions.length}{" "}
            varying parts, {review.from_surface} are named by the file&apos;s own
            text{review.from_model > 0
              ? `, ${review.from_model} by a model (${review.provider})`
              : ""}, and {review.unnamed} are open.
            {review.reason === "PROVIDER_FAILED"
              && " The model could not be reached, so only the file's own text has been read."}
            {review.reason === "UNREADABLE_REPLY"
              && " The model's answer could not be read, so only the file's own text has been read."}
          </Alert>
          {p.unclaimed > 0 && p.unclaimed_samples.length > 0 && (
            <Alert severity="warning" sx={{ mb: 1.5 }}>
              Rows no shape matched, as examples:{" "}
              {p.unclaimed_samples.slice(0, 4).join("  ·  ")}
            </Alert>
          )}
          <TextField select size="small" label="Decimal separator" value={decimal}
                     disabled={busy} sx={{ mb: 1.5, minWidth: 260 }}
                     onChange={(e) => onDecimal(e.target.value)}
                     helperText="Whether 11,1 in this file is eleven point one or eleven thousand one hundred. Not answerable from the text — both readings parse.">
            <MenuItem value="either">either . or , is the decimal point</MenuItem>
            <MenuItem value="dot">. is the decimal point</MenuItem>
            <MenuItem value="comma">, is the decimal point</MenuItem>
          </TextField>
          <BindingReviewGrid suggestions={review.suggestions} edits={edits}
                             onEdit={onEdit} />
        </>
      )}
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
function DecodingDialog({ source, ruleSets, busy, onClose, onSave, onAnalyze,
                         onPropose }: {
  source: CompanySource;
  ruleSets: { id: string; path: string }[];
  busy: boolean;
  onClose: () => void;
  onSave: (config: { record_id: string; description: string;
                     grade?: string | null; rule_set?: string | null;
                     decoder?: DecoderArtifact | null;
                     bindings?: BindingChoice[] | null;
                     decimal?: string | null }) => void;
  onAnalyze: () => void;
  onPropose: () => Promise<DecoderProposalResponse>;
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
  // Which path this dialog is composing. Opens on what the file already uses,
  // and on the rule set otherwise — the state a file has always started in.
  const [path, setPath] = useState<"rule_set" | "decoder">(
    d.path === "decoder" ? "decoder" : "rule_set");
  const [proposal, setProposal] = useState<DecoderProposalResponse | null>(null);
  const [proposing, setProposing] = useState(false);
  const [proposeError, setProposeError] = useState<string | null>(null);
  // The person's answers, over the proposal's. Kept apart from the proposal
  // rather than folded into it, so "what a machine suggested" and "what
  // somebody confirmed" stay distinguishable while the dialog is open — and
  // re-proposing does not silently keep edits made against different groups.
  const [edits, setEdits] = useState<Record<string, BindingEdit>>({});
  const [decimal, setDecimal] = useState<string>(d.decoder?.decimal ?? "either");

  async function propose() {
    setProposing(true);
    setProposeError(null);
    try {
      const next = await onPropose();
      setProposal(next);
      setEdits({});
      setDecimal(next.proposal.decoder?.decimal ?? "either");
    } catch (e) {
      setProposeError(e instanceof Error ? e.message
                                         : "The proposal could not be read.");
    } finally {
      setProposing(false);
    }
  }

  /** The binding set as it stands: the proposal's answers with the person's
   *  over them, and only the rows that name a slot.
   *
   *  A row with no slot contributes nothing rather than a null binding — an
   *  unbound group is a group the decoder does not read, which is a different
   *  thing from one it reads as nothing. */
  function bindings(): BindingChoice[] {
    const out: BindingChoice[] = [];
    for (const sg of proposal?.review?.suggestions ?? []) {
      const edit = edits[`${sg.segment}/${sg.group}`];
      const slot = edit?.slot ?? sg.slot ?? "";
      const type = edit?.type ?? sg.type ?? "";
      if (!slot || !type) continue;
      out.push({ segment: sg.segment, group: sg.group, slot,
                 type: type as BindingChoice["type"] });
    }
    return out;
  }

  const decoderReady = Boolean(proposal?.proposal.decoder);

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
    // Wider for the review, which is a grid of every varying part in the
    // file; the columns form alone reads better narrow.
    <Dialog open onClose={onClose} fullWidth
            maxWidth={path === "decoder" ? "lg" : "sm"}>
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
        </Stack>

        {/* Two paths, and a config names exactly one. A toggle rather than a
            pair of sections, because the server refuses a config naming both:
            a form that can express only one answer is the honest shape of a
            rule that says only one is allowed. */}
        <Box sx={{ mt: 3 }}>
          <Typography variant="subtitle2" sx={{ mb: 1 }}>
            What decodes the descriptions?
          </Typography>
          <ToggleButtonGroup exclusive size="small" value={path}
                             disabled={busy}
                             onChange={(_e, v) => v && setPath(v)}
                             aria-label="How this file's descriptions are decoded">
            <ToggleButton value="rule_set">A rule set the engine ships</ToggleButton>
            <ToggleButton value="decoder">A decoder built from this file</ToggleButton>
          </ToggleButtonGroup>
        </Box>

        {path === "rule_set" ? (
          <>
            <TextField select fullWidth size="small" label="Rule set" value={ruleSet}
                       sx={{ mt: 2.5 }}
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
              {/* A rule set this file names and the pinned engine no longer
                  has. Offered rather than dropped: silently blanking the menu
                  would make a stored choice look like no choice at all. */}
              {d.rule_set && !ruleSets.some((r) => r.id === d.rule_set) && (
                <MenuItem value={d.rule_set}>
                  {d.rule_set} — not shipped by this engine
                </MenuItem>
              )}
            </TextField>
          </>
        ) : (
          <DecoderPanel source={source} busy={busy} proposal={proposal}
                        proposing={proposing} error={proposeError} edits={edits}
                        decimal={decimal} onPropose={propose}
                        onDecimal={setDecimal}
                        onEdit={(id, patch) => setEdits((prev) =>
                          nextEdits(prev, id, patch,
                                    typesForGroup(proposal, id)))} />
        )}
        </>
        )}
        {/* Outside the columns guard, and outside the path branch when there
            are none. A file stored before its headers were read has nothing to
            choose *from*, and re-analysing is the only thing that helps it —
            nesting this under the rule-set panel took that button away from
            exactly the file that needs it, which is the state this dialog's
            first branch exists to explain. */}
        {(columns.length === 0 || path === "rule_set") && (
          <AnalysisEvidence source={source} busy={busy} onAnalyze={onAnalyze} />
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>
          {columns.length === 0 ? "Close" : "Cancel"}
        </Button>
        {columns.length > 0 && (
        <Button variant="contained"
                disabled={busy || !recordId || !description
                          || (path === "decoder" && !decoderReady)}
                onClick={() => onSave(
                  path === "decoder"
                    ? { record_id: recordId, description, grade: grade || null,
                        rule_set: null,
                        decoder: proposal?.proposal.decoder ?? null,
                        bindings: bindings(), decimal }
                    // Sending `decoder: null` explicitly rather than omitting
                    // it: a person switching this file back to a rule set is
                    // saying the decoder no longer decodes it, and a config
                    // that kept both would be one the server refuses.
                    : { record_id: recordId, description, grade: grade || null,
                        rule_set: ruleSet || null, decoder: null })}>
          Save decoding config
        </Button>
        )}
      </DialogActions>
    </Dialog>
  );
}

export function CatalogSources({ catalogue, label, ruleSets, canManage, busy,
                                onUpload, onSaveDecoding, onAnalyze, onPropose,
                                onRemove }: {
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
                             rule_set?: string | null;
                             decoder?: DecoderArtifact | null;
                             bindings?: BindingChoice[] | null;
                             decimal?: string | null }) => void;
  onAnalyze: (sourceKey: string) => void;
  /** Read one file and propose a decoder for it. Returns the proposal rather
   *  than going through the caller that replaces catalogue state, because it
   *  saves nothing — a proposal that quietly changed this screen would be one
   *  nobody had confirmed. */
  onPropose: (sourceKey: string) => Promise<DecoderProposalResponse>;
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
                           tip={p.data.decoding.path
                             ? `Decoded through ${decodedBy(p.data)}.`
                             : "Nothing decodes this file yet, so a build will name it rather than decode it."} />;
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
          onPropose={() => onPropose(decoding.source_key)}
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
