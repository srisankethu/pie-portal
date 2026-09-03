// The decoded product catalogues, per connected company.
//
// Until this screen existed the catalogue was a terminal step — `python
// scripts/build_catalog.py` — with no way to see from the product whether one
// existed, how old it was, or which pack and ruleset produced it. Those last
// two are the point, not decoration: `run_id` derives from the input bytes
// plus the ruleset checksum, so the stamp shown here is what says WHICH
// catalogue answered a given resolution.
//
// It began as a panel at the bottom of Data & connection, which is where it
// belongs by subject — but that screen is already tall, and a build control
// nobody scrolls to is the same as no build control. It is its own address
// under Setup now. Not named "catalogue": that nav item already exists and
// assigns an item to a line of the business, which is a different question
// from how the manufacturer encodes a part number.
//
// One organization can read three companies' books, and those three have three
// different item masters — and each of those companies sells several
// manufacturers' ranges. A pack is one manufacturer's decoder: Kennametal's
// price lists read through Kennametal's pack and YG-1's through YG-1's, and a
// single pack over both files would quarantine half of them. So a company
// keeps **one catalogue per manufacturer** — its own files, its own pack, its
// own build and stamp — and resolves against the **union** of everything it
// has built. The union is the server's: it is assembled from the built files,
// its record count and its duplicate count come from its own manifest, and
// this screen reports them once per company rather than adding the
// catalogues up. A part number two catalogues both claim is kept from the
// most recently built one, and that is stated here because it is a policy
// rather than an accident. A quote resolves against the union of the company
// it was raised from — never against another company's. A company that has
// built nothing resolves nothing, which the chip says in those words.
//
// Every number rendered comes from the parser's own run report, stored with the
// catalogue at build time and served verbatim. Nothing is recomputed here or on
// the server — a second parse-rate calculation is exactly the drift CLAUDE.md
// §2 warns about.
//
// NOT BUILT is its own state, never 0%. A missing catalogue says nothing about
// coverage — that distinction (`pie_service.catalog_available`) is load-bearing
// in the coverage reports, and this screen keeps it.
//
// The row count here is the number of companies a business keeps, and within
// one the number of manufacturers it sells — both properties of its structure
// rather than of its size — so these are per-company Paper surfaces like
// `ConnectionsPanel`'s, with a section per catalogue, not a grid. The *files*
// inside one catalogue are a grid (`CatalogSources`), for the opposite
// reason: how many exports a business keeps grows with the business.
//
// Busy, problem and pack-trial state are keyed by company **and** catalogue.
// One catalogue building must not freeze the others in the same company —
// they are different files through different packs, and the server serialises
// the builds itself. A company-level action (adding a catalogue) is keyed by
// the company alone.
//
// Two things this screen got wrong, both fixed here and both the same mistake.
//
// The Pack control was a `Select` over whatever `/catalog/companies` returned.
// Where the engine ships no packs — `deploy/backend.Dockerfile` builds an image
// without the private submodule on purpose, and says so — that is an empty
// dropdown that opens onto nothing. The server has always sent the reason in
// `source.reason`; this screen simply never rendered it, so the one state a
// person cannot fix by clicking harder looked like a broken menu. An empty
// control that does not say why is the interface version of the benign default
// CLAUDE.md §1 forbids.
//
// And a pack was chosen by its identifier. `zcnc` says nothing about whether it
// reads *your* export, so `pack-fit` runs each shipped pack over a sample of
// this catalogue's own files and reports the parser's counts for each. The
// counts are the parser's; nothing here computes a score, because a fit number
// this screen invented would be a second parse-rate calculation next to the
// one `catalog.run_parse` already refuses to duplicate.

import { useCallback, useEffect, useState } from "react";
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
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

import { papi } from "./api";
import { CatalogSources, fileSize, megabytes } from "./CatalogSources";
import { EmptyState, ErrorState, LoadingState, PercentageValue, StatusChip } from "./kit";
import type { CatalogueUnion, CompanyCatalogue, CompanyCatalogueEntry,
              CompanyCatalogues as View, PackFit, PlatformSession } from "./types";
import { CatalogLearning } from "./CatalogLearning";
import { Bp, Labelled, Tip } from "./ui";
import { formatDateTime, since } from "../when";

type Tone = "good" | "warn" | "bad" | "neutral";
type Pack = { id: string; path: string };

/** The one state word for a catalogue, from the facts the server sends.
 *
 *  Ordered by what has to be fixed first: without a pack or a corpus there is
 *  nothing to build, so those are named ahead of NOT BUILT rather than
 *  collapsed into it. "You have not built it" is not useful advice to someone
 *  who has nothing to build it from. */
function chipFor(c: CompanyCatalogueEntry): { label: string; tone: Tone } {
  if (!c.pack_id) return { label: "NO PACK CHOSEN", tone: "neutral" };
  if (!c.pack_resolved) return { label: "PACK NOT FOUND", tone: "bad" };
  if (c.sources.length === 0) return { label: "NO EXPORT", tone: "neutral" };
  if (c.built_but_missing_on_disk) return { label: "NEEDS REBUILD", tone: "warn" };
  if (!c.exists) return { label: "NOT BUILT", tone: "warn" };
  if (c.stale) return { label: "OUT OF DATE", tone: "warn" };
  return { label: "READY", tone: "good" };
}

/** The one state word for a company: what it resolves against, in records.
 *
 *  The count is the union's own, from its manifest — not the catalogues added
 *  up, which would double-count a part number two of them share. A company
 *  with a union resolves; one without resolves nothing, and NOTHING BUILT says
 *  so rather than reading as zero coverage. */
function unionChip(c: CompanyCatalogue): { label: string; tone: Tone } {
  if (!c.union) return { label: "NOTHING BUILT", tone: "neutral" };
  return { label: `RESOLVES ${c.union.records} RECORDS`, tone: "good" };
}

/** What a catalogue is called on screen: its name, or its key until it has one.
 *  A catalogue migrated from before names existed arrives with an empty name. */
function nameOf(c: CompanyCatalogueEntry): string {
  return c.name || c.catalogue_key;
}

/** The key busy, problem and pack-trial state are held under. A company-level
 *  action uses the connection id alone. */
function keyOf(c: CompanyCatalogueEntry): string {
  return `${c.connection_id}/${c.catalogue_key}`;
}

/** Every shipped pack, tried against a sample of one catalogue's own files.
 *
 *  The counts are the parser's. This panel deliberately does **not** rank the
 *  packs or compute a fit percentage: the numbers that would go into such a
 *  score are the ones `catalog.run_parse` already refuses to recompute, and a
 *  single "83% fit" would hide the distinction that actually decides the
 *  choice — a pack that classifies few rows is wrong for this export, and a
 *  pack that errors could not read it at all. Those are different answers.
 *
 *  Quarantined is shown beside classified rather than subtracted from it,
 *  because "unknown means unknown" is the engine's contract: a quarantined row
 *  is kept, and a pack that quarantines most of a file has told you something.
 */
function PackFitPanel({ fit, chosen, onChoose, onClose }: {
  fit: PackFit;
  chosen: string | null;
  onChoose: (packId: string) => void;
  onClose: () => void;
}) {
  if (!fit.available) {
    return (
      <Alert severity="info" sx={{ mb: 1.5 }} onClose={onClose}>
        <AlertTitle>Nothing to try a pack against</AlertTitle>
        {fit.reason}
      </Alert>
    );
  }
  return (
    <Alert severity="info" sx={{ mb: 1.5 }} onClose={onClose}
           icon={false}>
      <AlertTitle>
        Each pack over the first {fit.sample_rows} rows of these files
      </AlertTitle>
      {/* A fact panel, not a grid: the row count is the pack vocabulary the
          pinned engine ships — a property of the engine, fixed for a given
          deployment — which is the case ui-standards §3 keeps as a table. */}
      <table className="facttable" aria-label="Parse counts per pack">
        <tbody>
          {fit.packs.map((p) => (
            <tr key={p.pack_id}>
              <td>
                {p.pack_id}
                {p.pack_id === chosen && (
                  <span className="fsrc" style={{ marginLeft: 6 }}>chosen</span>
                )}
              </td>
              <td className="fv">
                {p.error ? (
                  <span>could not read these files — {p.error}</span>
                ) : (
                  <>
                    {p.classified} classified, {p.quarantined} quarantined
                    <span className="fsrc" style={{ marginLeft: 8 }}>
                      of {p.rows_read} rows
                    </span>
                    {p.pack_id !== chosen && (
                      <Button size="small" sx={{ ml: 1 }}
                              onClick={() => onChoose(p.pack_id)}>
                        Use this
                      </Button>
                    )}
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Alert>
  );
}

/** Define a catalogue: the manufacturer's name, and the pack that decodes it.
 *
 *  Nothing is uploaded or built here — this only creates the thing the files
 *  and the build then belong to. The pack may be left for later, because the
 *  honest way to choose one is the trial, and the trial needs a file first.
 *  With exactly one shipped pack it is pre-chosen: there is nothing to choose
 *  between, and a required empty menu would be a step for its own sake. */
function AddCatalogueDialog({ packs, busy, onClose, onAdd }: {
  packs: Pack[];
  busy: boolean;
  onClose: () => void;
  onAdd: (name: string, packId: string | null) => void;
}) {
  const [name, setName] = useState("");
  const [pack, setPack] = useState(packs.length === 1 ? packs[0].id : "");
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="xs">
      <DialogTitle>Add a catalogue</DialogTitle>
      <DialogContent>
        <DialogContentText sx={{ mb: 2 }}>
          One per manufacturer this company sells — Kennametal, YG-1 — because
          each one&apos;s price lists decode through that manufacturer&apos;s
          own pack. The company resolves against all of them at once.
        </DialogContentText>
        <Stack spacing={2.5} sx={{ mt: 1 }}>
          <TextField autoFocus fullWidth size="small" label="Name" value={name}
                     disabled={busy} required
                     helperText="The manufacturer, as the screen should call it."
                     onChange={(e) => setName(e.target.value)} />
          <TextField select fullWidth size="small" label="Pack" value={pack}
                     disabled={busy || packs.length === 0}
                     helperText={packs.length === 0
                       ? "none available"
                       : "The decoder for this manufacturer's part numbers. Can be chosen after a file is uploaded, on the parser's own counts."}
                     onChange={(e) => setPack(e.target.value)}>
            <MenuItem value="">— choose later —</MenuItem>
            {packs.map((p) => <MenuItem key={p.id} value={p.id}>{p.id}</MenuItem>)}
          </TextField>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button variant="contained" disabled={busy || !name.trim()}
                onClick={() => onAdd(name.trim(), pack || null)}>
          Add
        </Button>
      </DialogActions>
    </Dialog>
  );
}

/** Give a catalogue the name the screen shows it under. The key stays. */
function RenameDialog({ catalogue, busy, onClose, onRename }: {
  catalogue: CompanyCatalogueEntry;
  busy: boolean;
  onClose: () => void;
  onRename: (name: string) => void;
}) {
  const [name, setName] = useState(catalogue.name);
  const unchanged = name.trim() === catalogue.name;
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="xs">
      <DialogTitle>Rename {nameOf(catalogue)}</DialogTitle>
      <DialogContent>
        <DialogContentText sx={{ mb: 2 }}>
          Only what this screen calls it changes. Its key,{" "}
          <span style={{ fontFamily: "var(--font-mono, monospace)" }}>
            {catalogue.catalogue_key}
          </span>, is its address on disk and in every union row, and stays.
        </DialogContentText>
        <TextField autoFocus fullWidth size="small" label="Name" value={name}
                   disabled={busy} sx={{ mt: 1 }}
                   onChange={(e) => setName(e.target.value)} />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button variant="contained" disabled={busy || !name.trim() || unchanged}
                onClick={() => onRename(name.trim())}>
          Save
        </Button>
      </DialogActions>
    </Dialog>
  );
}

/** What the company resolves against, once — the union's own facts.
 *
 *  Stated at the company rather than under any one catalogue because it is a
 *  fact about all of them together: a resolution is answered from the union
 *  file, and the retrieval index is built beside that file, not beside each
 *  catalogue. A fact panel — a fixed handful of rows — per ui-standards §3. */
function UnionFacts({ union, total }: { union: CatalogueUnion; total: number }) {
  const built = union.catalogues.length;
  const of = built === total ? "" : ` of ${total}`;
  return (
    <Box sx={{ mb: 1.5 }}>
      <table className="facttable" aria-label="What this company resolves against">
        <tbody>
          <tr>
            <td>
              <Labelled tip="Every built catalogue this company keeps, merged into the one file its resolutions read. The count is the union's own, from its manifest — not the catalogues added up, which would count a part number two of them share twice.">
                Resolves against
              </Labelled>
            </td>
            <td className="fv">
              {union.records} records from {built}{of} catalogue{built === 1 ? "" : "s"}
              {union.version && (
                <div className="fsrc" style={{ fontFamily: "var(--font-mono, monospace)" }}>
                  union {union.version}
                </div>
              )}
            </td>
          </tr>
          <tr>
            <td>
              <Labelled tip="A line the engine cannot rank — a series named in words, a request with no ISO code in it — is also searched by description: the records whose text reads most like the line are offered as possibilities beneath the engine's own ranking, after the engine has compared them. The model id and record count here are what say which index found a given option. Built beside the union, so it spans every catalogue.">
                Retrieval index
              </Labelled>
            </td>
            <td className="fv">
              {union.retrieval ? (
                <>
                  {union.retrieval.model_id}
                  <div className="fsrc">
                    {union.retrieval.records} records indexed
                    {!union.retrieval.current && " · behind the union, rebuilt on next use"}
                  </div>
                </>
              ) : (
                <>
                  none yet
                  <div className="fsrc">built on first use</div>
                </>
              )}
            </td>
          </tr>
        </tbody>
      </table>
      {union.duplicates > 0 && (
        <Alert severity="info" sx={{ mt: 1 }}>
          {union.duplicates} part number{union.duplicates === 1 ? "" : "s"} appear
          {union.duplicates === 1 ? "s" : ""} in more than one catalogue — the most
          recently built catalogue&apos;s row was used for each.
          {union.duplicate_examples.length > 0 &&
            ` For example ${union.duplicate_examples.slice(0, 5).join(", ")}.`}
        </Alert>
      )}
    </Box>
  );
}

/** Everything one catalogue can be asked to do, bound to it by the parent so
 *  the section itself never spells a URL or holds a token. */
interface CatalogueActions {
  setPack: (packId: string) => void;
  tryPacks: () => void;
  build: () => void;
  upload: (file: File, sourceKey?: string) => void;
  map: (sourceKey: string, mapping: { record_id: string; description: string;
                                      grade?: string | null }) => void;
  removeSource: (sourceKey: string) => void;
  rename: (name: string) => void;
  remove: () => void;
}

/** One manufacturer's catalogue inside its company's surface: state, controls,
 *  files, and the facts a resolution's stamp is read against.
 *
 *  Not its own `Bp`. A Paper inside a Paper is a surface with no meaning of
 *  its own; a rule between sections says "same company, next manufacturer"
 *  without suggesting the catalogue could be opened as a thing in itself. */
function CatalogueSection({ company, catalogue, packs, canManage, busy, problem,
                            fit, on, onCloseFit, onCloseProblem }: {
  company: CompanyCatalogue;
  catalogue: CompanyCatalogueEntry;
  packs: Pack[];
  canManage: boolean;
  busy: boolean;
  problem: string | null;
  fit: PackFit | null;
  on: CatalogueActions;
  onCloseFit: () => void;
  onCloseProblem: () => void;
}) {
  const c = catalogue;
  const chip = chipFor(c);
  const report = c.report;
  const label = company.label || company.connection_id;
  const [renaming, setRenaming] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  // Families the pack actually saw rows for, the parser's "(unresolved)"
  // census excluded — that count is shown as its own quarantine row.
  const families = report
    ? Object.entries(report.by_family).filter(([f]) => f !== "(unresolved)")
    : [];
  const newTokens = report ? Object.entries(report.new_tokens_top) : [];

  return (
    <Box sx={{ borderTop: 1, borderColor: "divider", pt: 1.5, mt: 1.5 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center",
                    flexWrap: "wrap", marginBottom: 8 }}>
        <b>{nameOf(c)}</b>
        <StatusChip label={chip.label} tone={chip.tone}
                    tip={c.exists
                      ? "This catalogue is built and on disk. It is part of what this company's quote lines resolve against."
                      : "This catalogue is not built, so nothing of this manufacturer's is in what the company resolves against — its part numbers answer UNKNOWN, not zero coverage, until it is."} />
        {c.exists && (
          <span className="st-help">
            {c.records} decoded records, built {since(c.built_at)}
          </span>
        )}
        <Box sx={{ flex: 1 }} />
        {canManage && (
          <>
            {/* Disabled where there is nothing to choose, and the reason is
                stated under the control rather than left to an empty menu.
                `helperText` on a disabled field is the one place a person is
                already looking when a dropdown does nothing. */}
            <TextField
              select size="small" label="Pack"
              sx={{ minWidth: 160 }}
              value={packs.some((p) => p.id === c.pack_id) ? c.pack_id : ""}
              disabled={busy || packs.length === 0}
              helperText={packs.length === 0 ? "none available" : undefined}
              onChange={(e) => on.setPack(e.target.value)}
            >
              {packs.map((p) => (
                <MenuItem key={p.id} value={p.id}>{p.id}</MenuItem>
              ))}
            </TextField>
            {/* Offered whenever there is a pack and a file, not only where
                there are several packs to choose between. With one shipped
                pack the question is not "which of these" but "does this one
                read my file at all" — and that is the more important of the
                two, because the answer decides whether a pack has to be
                written. */}
            {packs.length > 0 && c.sources.length > 0 && (
              <Button size="small" disabled={busy} onClick={on.tryPacks}>
                {packs.length > 1 ? "Which pack fits?" : "Try this pack"}
              </Button>
            )}
            <Button
              variant={c.exists ? "outlined" : "contained"} size="small"
              disabled={busy || c.sources.length === 0 || !c.pack_resolved}
              onClick={on.build}
            >
              {busy ? "Working…" : c.exists ? "Rebuild" : "Build"}
            </Button>
            <Button size="small" disabled={busy} onClick={() => setRenaming(true)}>
              Rename
            </Button>
            <Button size="small" color="error" disabled={busy}
                    onClick={() => setConfirmRemove(true)}>
              Remove catalogue
            </Button>
          </>
        )}
      </div>
      {busy && <LinearProgress sx={{ mb: 1 }} />}

      {problem && (
        <Alert severity="error" sx={{ mb: 1.5 }} onClose={onCloseProblem}>
          <AlertTitle>That did not work</AlertTitle>
          {problem}
        </Alert>
      )}
      {c.stale && (
        <Alert severity="warning" sx={{ mb: 1.5 }}>
          This catalogue was built from a different set of files than the
          ones on record now — one has been added, replaced or removed.
          It is still a real catalogue with a real stamp; it is just not
          built from what this catalogue now holds. Rebuild to decode the
          current set.
        </Alert>
      )}
      {c.built_but_missing_on_disk && (
        <Alert severity="warning" sx={{ mb: 1.5 }}>
          This catalogue was built but its decoded file is not on disk —
          usually a redeploy. The export it was built from is stored, so
          rebuilding restores it exactly.
        </Alert>
      )}
      {fit && (
        <PackFitPanel fit={fit} chosen={c.pack_id} onClose={onCloseFit}
                      onChoose={(packId) => { onCloseFit(); on.setPack(packId); }} />
      )}

      <Box sx={{ mb: 1.5 }}>
        <CatalogSources
          catalogue={c} label={label} canManage={canManage} busy={busy}
          onUpload={on.upload} onMap={on.map} onRemove={on.removeSource}
        />
      </Box>

      <div className="dp-split-2">
        {/* A fact panel — a label and a value, a fixed handful of rows —
            which is the case ui-standards §3 keeps as a plain table. */}
        <div>
          <table className="facttable">
            <tbody>
              <tr>
                <td>
                  <Labelled tip="The files this catalogue is decoded from — a manufacturer's price list, an item master, a range extension. Stored as records rather than files, so they survive a redeploy and the catalogue can always be rebuilt from them.">
                    Export files
                  </Labelled>
                </td>
                {/* The set rather than one filename: the grid above names the
                    files, so repeating the newest one here would be the same
                    fact stated twice and stale half the time. */}
                <td className="fv">
                  {c.sources.length === 0 ? "none uploaded" : (
                    <>
                      {c.sources.length === 1
                        ? c.sources[0].filename
                        : `${c.sources.length} files`}
                      <div className="fsrc">
                        {fileSize(c.sources.reduce(
                          (t, x) => t + x.size_bytes, 0))} · newest{" "}
                        {formatDateTime(c.corpus?.uploaded_at ?? null)}
                      </div>
                    </>
                  )}
                </td>
              </tr>
              <tr>
                <td>
                  <Labelled tip="The decoder: the rules that read how this manufacturer phrases a description. Chosen from what the engine ships — never uploaded, because a pack is regexes the engine runs over every row of a file, and a pattern that backtracks catastrophically would be an outage a tenant could upload for themselves. Which one fits is answerable: 'Which pack fits?' runs each of them over a sample of these files.">
                    Pack
                  </Labelled>
                </td>
                <td className="fv">
                  {c.pack_id ?? "not chosen"}
                  {c.pack_id && !c.pack_resolved && (
                    <div className="fsrc">
                      this engine does not ship a pack by that name
                    </div>
                  )}
                  {c.exists && c.stamp.pack_id && (
                    <div className="fsrc">
                      nomenclature {c.stamp.pack_id} v{c.stamp.pack_version}
                      {c.stamp.org_id && ` · org layer ${c.stamp.org_id} v${c.stamp.org_version}`}
                    </div>
                  )}
                </td>
              </tr>
              {c.exists && (
                <>
                  <tr>
                    <td>
                      <Labelled tip="The pack's content hash. run_id derives from the input bytes plus this checksum, so this value — with the pack version — is what says which catalogue answered a given resolution.">
                        Ruleset checksum
                      </Labelled>
                    </td>
                    <td className="fv" style={{ fontFamily: "var(--font-mono, monospace)" }}>
                      {c.stamp.ruleset_checksum}
                    </td>
                  </tr>
                  <tr>
                    <td>Run id<div className="fsrc">input bytes + ruleset</div></td>
                    <td className="fv" style={{ fontFamily: "var(--font-mono, monospace)" }}>
                      {c.stamp.run_id}
                    </td>
                  </tr>
                  <tr>
                    <td>Built</td>
                    <td className="fv">
                      {formatDateTime(c.built_at)}
                      {c.duration_s != null && (
                        <div className="fsrc">took {c.duration_s}s</div>
                      )}
                    </td>
                  </tr>
                  <tr>
                    <td>
                      <Labelled tip="Rows the parse read, after the files were merged and rows with no part number or no description were left out. Where several files carry the same part number the newest file's row is the one read.">
                        Rows read
                      </Labelled>
                    </td>
                    <td className="fv">
                      {c.rows_read}
                      {c.built_from && c.built_from.length > 1 && (
                        <div className="fsrc">
                          merged from {c.built_from.length} files
                          {c.ingest && c.ingest.collisions > 0 &&
                            `, ${c.ingest.collisions} overlapping`}
                        </div>
                      )}
                    </td>
                  </tr>
                  <tr><td>Classified</td><td className="fv">{c.records}</td></tr>
                  <tr>
                    <td>
                      <Labelled tip="Rows the pack could not place in any family. Kept beside the catalogue rather than dropped — unknown means unknown.">
                        Quarantined
                      </Labelled>
                    </td>
                    <td className="fv">{c.quarantined}</td>
                  </tr>
                  {report && (
                    <tr>
                      <td>
                        <Labelled tip="Tokens the grammars did not recognise, captured verbatim by the parser. A growing list here is the signal a pack needs new vocabulary.">
                          New tokens captured
                        </Labelled>
                      </td>
                      <td className="fv">
                        {newTokens.length === 0 ? "none" : newTokens.length}
                        {newTokens.length > 0 && (
                          <div className="fsrc">
                            {newTokens.slice(0, 8).map(([t, n]) => `${t} (${n})`).join(", ")}
                            {newTokens.length > 8 && ", …"}
                          </div>
                        )}
                      </td>
                    </tr>
                  )}
                  <tr>
                    <td>Engine</td>
                    <td className="fv">
                      v{c.stamp.engine_version}
                      <div className="fsrc">schema v{c.stamp.schema_version}</div>
                    </td>
                  </tr>
                </>
              )}
            </tbody>
          </table>
        </div>

        {/* Per-family census and parse rate, straight from the parser's run
            report. The row count is the pack's declared family vocabulary — a
            property of the pack's structure, not of the size of the business
            — which is the fixed-shape case that stays a plain table rather
            than a DataGrid. */}
        {c.exists && report && (
          <div>
            <table className="facttable"
                   aria-label={`Per-family parse rates for ${label} · ${nameOf(c)}`}>
              <tbody>
                {families.map(([family, count]) => (
                  <tr key={family}>
                    <td>{family}</td>
                    <td className="fv">
                      {count}
                      <span className="fsrc" style={{ marginLeft: 8 }}>
                        <PercentageValue value={report.parse_rates[family]} digits={1} />
                      </span>
                    </td>
                  </tr>
                ))}
                {report.unresolved_family > 0 && (
                  <tr>
                    <td>(no family resolved)</td>
                    <td className="fv">{report.unresolved_family}</td>
                  </tr>
                )}
              </tbody>
            </table>
            <p className="st-help" style={{ marginTop: 6 }}>
              Rows per family, with the grammar parse rate beside each —
              the parser&apos;s own numbers, not recomputed here.
            </p>
          </div>
        )}
      </div>

      {renaming && (
        <RenameDialog catalogue={c} busy={busy}
                      onClose={() => setRenaming(false)}
                      onRename={(name) => { setRenaming(false); on.rename(name); }} />
      )}
      <Dialog open={confirmRemove} onClose={() => setConfirmRemove(false)}>
        <DialogTitle>Stop resolving against {nameOf(c)}?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            {label} stops resolving against this manufacturer at once — no
            rebuild needed — and its part numbers answer UNKNOWN from now on.
            The files it was built from are kept and superseded rather than
            deleted, so what was decoded stays on record; only the decoded
            output goes.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmRemove(false)}>Cancel</Button>
          <Button color="error" variant="contained" onClick={() => {
            setConfirmRemove(false);
            on.remove();
          }}>Remove</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}

export function CatalogScreen({ session }: { session: PlatformSession }) {
  const [view, setView] = useState<View | null>(null);
  const [error, setError] = useState<string | null>(null);
  /** Which catalogue — or, for a company-level action, which company — is
   *  mid-action, so only its own controls are disabled. One catalogue building
   *  must not freeze the others, in its company or in another. */
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<{ id: string; message: string } | null>(null);
  /** The trial's result, for the one catalogue it was asked about. Not stored
   *  per catalogue: it is a sample measured a moment ago against files that
   *  can change, so keeping several around would show two answers of
   *  different ages side by side. */
  const [fit, setFit] = useState<{ id: string; result: PackFit } | null>(null);
  /** Which company the add-a-catalogue dialog is open for. */
  const [adding, setAdding] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setView(await papi.companyCatalogues(session.token));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [session.token]);

  useEffect(() => {
    load();
  }, [load]);

  /** Swap one company for the state the server just returned. Every mutating
   *  call answers with the whole company, because a build or a removal
   *  changes the union as well as the catalogue it touched. */
  function replace(updated: CompanyCatalogue) {
    setView((v) => (v ? {
      ...v,
      companies: v.companies.map((c) =>
        c.connection_id === updated.connection_id ? updated : c),
    } : v));
  }

  /** One request against one key: only that key's controls go quiet, and a
   *  failure is attributed to it rather than to the page.
   *
   *  Generic over the result because two callers need it and they do different
   *  things with what comes back — an action returns the company's finished
   *  state, the pack trial returns a measurement. The same six lines written
   *  twice would be six lines that eventually disagree about which of the two
   *  clears `busy`.
   */
  async function acting<T>(id: string, action: () => Promise<T>,
                          done: (result: T) => void) {
    setBusy(id);
    setProblem(null);
    try {
      done(await action());
    } catch (e) {
      // The server names the specific cause — a column the file lacks, a pack
      // the engine no longer ships, a name that yields no key — so it is
      // shown verbatim rather than summarised away.
      setProblem({ id, message: (e as Error).message });
    } finally {
      setBusy(null);
    }
  }

  /** An action that returns the company's finished state, so the company is
   *  replaced from the response rather than re-fetching the whole list. */
  function run(id: string, action: () => Promise<CompanyCatalogue>) {
    return acting(id, action, replace);
  }

  /** Run the trial for one catalogue. Shares `busy` with the actions, because
   *  it is a parse per pack and a build queued behind it would sit on the
   *  lock. */
  function tryPacks(c: CompanyCatalogueEntry) {
    setFit(null);
    const id = keyOf(c);
    return acting(id, () => papi.companyPackFit(
      session.token, c.connection_id, c.catalogue_key),
      (result) => setFit({ id, result }));
  }

  /** Every action one catalogue's section can take, bound to it here so the
   *  section never spells a URL or holds the token. */
  function actionsFor(c: CompanyCatalogueEntry): CatalogueActions {
    const t = session.token;
    const id = keyOf(c);
    const { connection_id: cid, catalogue_key: key } = c;
    return {
      setPack: (packId) => run(id, () => papi.setCompanyPack(t, cid, key, packId)),
      tryPacks: () => tryPacks(c),
      build: () => run(id, () => papi.buildCompanyCatalog(t, cid, key)),
      upload: (file, sourceKey) =>
        run(id, () => papi.uploadCompanyCorpus(t, cid, key, file, sourceKey)),
      map: (sourceKey, mapping) =>
        run(id, () => papi.setSourceMapping(t, cid, key, sourceKey, mapping)),
      removeSource: (sourceKey) =>
        run(id, () => papi.removeCompanySource(t, cid, key, sourceKey)),
      rename: (name) => run(id, () => papi.renameCompanyCatalogue(t, cid, key, name)),
      remove: () => run(id, () => papi.removeCompanyCatalogue(t, cid, key)),
    };
  }

  // The heading is part of every branch, not only the loaded one: as its own
  // address this screen can be opened cold, and a page that renders a bare
  // error box with no title does not say which screen failed.
  const head = (
    <div className="dp-head">
      <h1>Decoded catalogue</h1>
      <p>
        What each company resolves a part number against — one decoded
        catalogue per manufacturer it sells, merged — and which build of it
        answered.
      </p>
    </div>
  );

  if (error) {
    return (
      <div>
        {head}
        <ErrorState title="Could not read the catalogues" error={error}
                    onRetry={load} />
      </div>
    );
  }
  if (!view) {
    return (
      <div>
        {head}
        <LoadingState rows={1} height={90} label="Reading each company's catalogues…" />
      </div>
    );
  }

  const packs = view.packs;
  const addingTo = adding
    ? view.companies.find((c) => c.connection_id === adding) ?? null : null;

  return (
    <div>
      {head}

      {view.companies.length === 0 ? (
        <Bp style={{ padding: "14px" }}>
          <p className="st-help" style={{ margin: 0 }}>
            No companies are connected yet. Add one on <b>Data &amp; connection</b>,
            then its catalogues can be built here.
          </p>
        </Bp>
      ) : view.companies.map((c) => {
        const chip = unionChip(c);
        const companyActing = busy === c.connection_id;
        const atCeiling = c.catalogues.length >= view.max_catalogues;
        return (
          <Bp key={c.connection_id} style={{ padding: "10px 14px 14px", marginBottom: 10 }}>
            <div style={{ display: "flex", gap: 8, alignItems: "center",
                          flexWrap: "wrap", marginBottom: 8 }}>
              <b>{c.label || c.connection_id}</b>
              <StatusChip label={chip.label} tone={chip.tone}
                          tip={c.union
                            ? "The union of every catalogue this company has built. Its quote lines resolve against this — never against another company's."
                            : "This company has built no catalogue. Its resolutions report UNKNOWN — not zero coverage — until one is built."} />
              <Box sx={{ flex: 1 }} />
              {view.can_manage && (
                <>
                  {atCeiling && (
                    <span className="fsrc">at the ceiling of {view.max_catalogues}</span>
                  )}
                  <Button size="small" variant="outlined"
                          disabled={companyActing || atCeiling}
                          onClick={() => setAdding(c.connection_id)}>
                    Add a catalogue
                  </Button>
                </>
              )}
            </div>
            {companyActing && <LinearProgress sx={{ mb: 1 }} />}

            {problem?.id === c.connection_id && (
              <Alert severity="error" sx={{ mb: 1.5 }}
                     onClose={() => setProblem(null)}>
                <AlertTitle>That did not work</AlertTitle>
                {problem.message}
              </Alert>
            )}
            {packs.length === 0 && view.can_manage && (
              // The state the empty dropdown used to render as nothing at all.
              // `source.reason` distinguishes an uninitialised submodule from a
              // present engine with a missing corpus, which are different
              // fixes. Once per company, not per catalogue: it is a fact about
              // the deployment, and saying it three times says it less.
              <Alert severity="warning" sx={{ mb: 1.5 }}>
                <AlertTitle>No pack is available to decode with</AlertTitle>
                A pack is the decoder the engine ships — the rules that read a
                part number and a description. This deployment has none, so
                nothing can be built until that is fixed; uploading a file here
                still works and it will decode once a pack is available.
                {view.source.reason ? ` ${view.source.reason}` : ""}
              </Alert>
            )}

            {c.union && <UnionFacts union={c.union} total={c.catalogues.length} />}

            {c.catalogues.length === 0 ? (
              <EmptyState
                title="No catalogue yet"
                reason={view.can_manage
                  ? "Add one per manufacturer this company sells — Kennametal, YG-1 — with Add a catalogue above. Each decodes that manufacturer's price lists through its own pack, and the company resolves against all of them. Until one is built, its resolutions answer UNKNOWN — not zero coverage."
                  : "Nothing has been defined for this company to decode. Its resolutions answer UNKNOWN — not zero coverage — until an owner adds a catalogue per manufacturer it sells, such as Kennametal or YG-1, and builds it."}
              />
            ) : c.catalogues.map((cat) => {
              const id = keyOf(cat);
              return (
                <CatalogueSection
                  key={id} company={c} catalogue={cat} packs={packs}
                  canManage={view.can_manage} busy={busy === id}
                  problem={problem?.id === id ? problem.message : null}
                  fit={fit?.id === id ? fit.result : null}
                  on={actionsFor(cat)}
                  onCloseFit={() => setFit(null)}
                  onCloseProblem={() => setProblem(null)}
                />
              );
            })}
          </Bp>
        );
      })}

      {addingTo && (
        <AddCatalogueDialog
          packs={packs} busy={busy === addingTo.connection_id}
          onClose={() => setAdding(null)}
          onAdd={(name, packId) => {
            setAdding(null);
            run(addingTo.connection_id, () => papi.createCompanyCatalogue(
              session.token, addingTo.connection_id, { name, pack_id: packId }));
          }} />
      )}

      <p className="st-help" style={{ marginTop: 8 }}>
        Nomenclature only — never price, cost or stock: a file&apos;s part
        number, description and grade are read, and every other column is left
        out of the catalogue rather than filtered out of it. CSV or Excel, up to
        {" "}{megabytes(view.max_corpus_bytes)} a file; up to{" "}
        {view.max_catalogues} catalogues a company.
        {!view.can_manage && " Uploading and building are owner actions."}
        <Tip text="A quote resolves against the union of the catalogues of the company it is raised from, and never against another company's — an answer from the wrong item master would carry a real provenance stamp for the wrong product." />
      </p>

      <CatalogLearning session={session} />
    </div>
  );
}
