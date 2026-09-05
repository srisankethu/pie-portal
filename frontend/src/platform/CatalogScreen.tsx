// The decoded product catalogues, per connected company.
//
// Until this screen existed the catalogue was a terminal step — `python
// scripts/build_catalog.py` — with no way to see from the product whether one
// existed, how old it was, or which rules and ruleset produced it. Those last
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
// manufacturers' ranges. A rule set is one manufacturer's decoder: Kennametal's
// price lists read through Kennametal's rules and YG-1's through YG-1's, and a
// single rule set over both files would quarantine half of them. So a company
// keeps **one catalogue per manufacturer** — its own files, its own build and
// stamp — and resolves against the **union** of everything it
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
// Busy and problem state are keyed by company **and** catalogue. One catalogue
// building must not freeze the others in the same company — they are different
// files through different rule sets, and the server serialises the builds
// itself. A company-level action (adding a catalogue) is keyed by the company
// alone.
//
// **There is no default decoder, and this screen is built around that.** A
// catalogue used to choose one pack and every file in it was decoded through
// that pack, which is wrong twice over: two exports of one manufacturer's range
// do not agree about their own column names, and a catalogue that acquires a
// second manufacturer's price list decodes it through the first one's grammars
// and gets a wrong catalogue carrying a real stamp. So the config belongs to
// the FILE. Every upload starts as an unknown format: it is analysed on its own
// evidence — its headers, and every shipped rule set run over its first rows —
// a config is *proposed*, a person checks it against the counts and saves it,
// and only then is that file decoded. AWAITING DECODING is the state in
// between, and the build refuses by name rather than falling back to anything.
//
// Every count on this screen and in that analysis is the parser's own, served
// verbatim. Nothing here ranks rule sets or computes a fit percentage: the
// numbers such a score would be made of are the ones `catalog.run_parse`
// already refuses to recompute, and one figure would hide the distinction that
// decides the choice — a rule set that classifies few rows is wrong for this
// file, one that errors could not read it at all, and none of them reading it
// means a rule set has to be written before this manufacturer decodes at all.
//
// The one control that still has to say why it is empty is the rule set menu.
// Where the engine ships none — `deploy/backend.Dockerfile` builds an image
// without the private submodule on purpose, and says so — an empty dropdown
// opens onto nothing. The server sends the reason in `source.reason`, and an
// empty control that does not say why is the interface version of the benign
// default CLAUDE.md §1 forbids.

import { useCallback, useEffect, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
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
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import { papi } from "./api";
import { CatalogSources, fileSize, megabytes } from "./CatalogSources";
import { EmptyState, ErrorState, LoadingState, PercentageValue, SectionHeader,
         StatusChip, type Tone } from "./kit";
import type { BindingChoice, CatalogueUnion, CompanyCatalogue,
              CompanyCatalogueEntry, CompanyCatalogues as View, DecoderArtifact,
              DecoderProposalResponse, PlatformSession } from "./types";
import { CatalogLearning } from "./CatalogLearning";
import { tokens } from "../theme";
import { Bp, Labelled, Tip } from "./ui";
import { formatDateTime, since } from "../when";

// `Tone` is imported rather than declared: this file had its own copy, one
// member short of `kit`'s, which is the small end of the duplication CLAUDE.md
// §2 is about — two vocabularies for one question.

type RuleSet = { id: string; path: string };

/** Monospace, for a hash somebody compares character by character.
 *
 *  Four places here wrote `fontFamily: "var(--font-mono, monospace)"`, and
 *  `theme.ts` does not publish `--font-mono` — `CSS_VARS` emits `--font-heading`
 *  and `--font-body` and stops there — so every one of them fell through to the
 *  browser's bare `monospace` and none of them used the token the theme
 *  actually holds. Read from `tokens.fontMono` instead, in one place, per
 *  ui-standards §11: a literal in a component is a value that will not follow. */
const MONO: CSSProperties = { fontFamily: tokens.fontMono };

/** The one state word for a catalogue, from the facts the server sends.
 *
 *  Ordered by what has to be fixed first: without a file, or with one nobody
 *  has said how to decode, there is nothing to build — so those are named
 *  ahead of NOT BUILT rather than collapsed into it. "You have not built it"
 *  is not useful advice to someone whose build would be refused by name. */
function chipFor(c: CompanyCatalogueEntry): { label: string; tone: Tone } {
  if (c.sources.length === 0) return { label: "NO EXPORT", tone: "neutral" };
  if (c.awaiting_decoding.length > 0) {
    return { label: "AWAITING DECODING", tone: "warn" };
  }
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

/** The key busy and problem state are held under. A company-level action uses
 *  the connection id alone. */
function keyOf(c: CompanyCatalogueEntry): string {
  return `${c.connection_id}/${c.catalogue_key}`;
}

/** The line a company or a catalogue is headed by: name, state, controls.
 *
 *  Written once because it was written twice — the same flex row, the same
 *  hardcoded `gap: 8` and `marginBottom: 8`, differing only in its children.
 *  That is ui-standards §10, and the spacing is now the theme's.
 *
 *  `level` is the part that earns the component. This screen has three rungs of
 *  structure — the page, a company, a manufacturer's catalogue inside it — and
 *  had one rung of type: both names were a bare `<b>`, so a reader scanning for
 *  "which company is this stamp under" had only the panel border to go on, on
 *  the one screen whose stated invariant is that a company's provenance must
 *  never be read against another's name. Company and catalogue now sit two
 *  rungs apart on the theme's own ramp (§4).
 *
 *  The catalogue's name stays a `<b>` element. Its section is delimited by the
 *  rule above it rather than by an outline level of its own, and the surrounding
 *  markup — the chip beside it, the controls after it — is what the assertions
 *  in `CatalogScreen.test.tsx` read off that element. */
function HeadingRow({ name, level, chip, meta, actions }: {
  name: string;
  level: "company" | "catalogue";
  chip: ReactNode;
  /** Secondary metadata, between the state word and the controls. */
  meta?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <Stack
      direction="row" spacing={1} useFlexGap
      sx={{ alignItems: "center", flexWrap: "wrap", mb: 1 }}
    >
      {level === "company"
        ? <Typography variant="h3" component="h2">{name}</Typography>
        : <Typography variant="h4" component="b">{name}</Typography>}
      {chip}
      {meta}
      <Box sx={{ flex: 1 }} />
      {actions}
    </Stack>
  );
}

/** One labelled half of the fact area under a catalogue.
 *
 *  These were two — later three — unlabelled tables sitting side by side, each
 *  with its explanation trailing underneath in a `<p>`. Labelled above and
 *  explained in the same breath, a reader knows which table they are in before
 *  they read a row of it, and the page loses a line rather than gaining one. */
function Pane({ title, note, children }: {
  title: string;
  /** What the numbers in it are, and — where it matters — what they are not. */
  note?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Box>
      {/* `component="p"`: `subtitle2` maps to an `<h6>` by default, and an h6
          under a company's `<h2>` would claim four outline levels this screen
          does not have. The tables carry their own `aria-label`, so the
          accessible name is not riding on this line. */}
      <Typography variant="subtitle2" component="p" color="text.secondary"
                  sx={{ mb: 0.5 }}>
        {title}
      </Typography>
      {children}
      {note && (
        <Typography variant="caption" color="text.secondary" component="p"
                    sx={{ mt: 0.75, mb: 0 }}>
          {note}
        </Typography>
      )}
    </Box>
  );
}

/** One action that failed, attributed to the thing it was tried on.
 *
 *  Two call sites — a company's action and a catalogue's — so it is written
 *  once. Not `kit.ErrorState`, which is this Alert with this title slot and no
 *  way to dismiss it: what this reports is one refused request beside controls
 *  that still work, not a screen that did not load, so it has to be closable. */
function ProblemAlert({ message, onClose }: {
  message: string;
  onClose: () => void;
}) {
  return (
    <Alert severity="error" sx={{ mb: 1.5 }} onClose={onClose}>
      <AlertTitle>That did not work</AlertTitle>
      {message}
    </Alert>
  );
}

/** Name the manufacturer a catalogue holds. Nothing else is decided here.
 *
 *  Nothing is uploaded, built or said about decoding: every price list
 *  uploaded into this catalogue brings its own config, worked out from that
 *  file alone. This only creates the thing the files and the build belong to.
 */
function AddCatalogueDialog({ busy, onClose, onAdd }: {
  busy: boolean;
  onClose: () => void;
  onAdd: (name: string) => void;
}) {
  const [name, setName] = useState("");
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="xs">
      <DialogTitle>Add a catalogue</DialogTitle>
      <DialogContent>
        <DialogContentText sx={{ mb: 2 }}>
          One per manufacturer this company sells — Kennametal, YG-1 — because
          each one phrases a part number and a description its own way. The
          company resolves against all of them at once. How each of its price
          lists is decoded is settled per file, after it is uploaded.
        </DialogContentText>
        {/* One field, so no `Stack` around it: a layout wrapper with a single
            child is the nesting ui-standards §1 asks to leave out. */}
        <TextField autoFocus fullWidth size="small" label="Name" value={name}
                   disabled={busy} required sx={{ mt: 1 }}
                   helperText="The manufacturer, as the screen should call it."
                   onChange={(e) => setName(e.target.value)} />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button variant="contained" disabled={busy || !name.trim()}
                onClick={() => onAdd(name.trim())}>
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
          <span style={MONO}>{catalogue.catalogue_key}</span>, is its address on
          disk and in every union row, and stays.
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
                <div className="fsrc" style={MONO}>union {union.version}</div>
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
  build: () => void;
  upload: (file: File, sourceKey?: string) => void;
  saveDecoding: (sourceKey: string,
                 config: { record_id: string; description: string;
                           grade?: string | null;
                           rule_set?: string | null;
                           decoder?: DecoderArtifact | null;
                           bindings?: BindingChoice[] | null;
                           decimal?: string | null }) => void;
  analyze: (sourceKey: string) => void;
  /** Read one file and propose a decoder for it. Not wrapped in `run`: it
   *  returns a proposal rather than a catalogue, and it saves nothing, so
   *  there is no new catalogue state for the screen to take on — and a
   *  proposal that replaced what is on screen would be one nobody had
   *  confirmed. The dialog holds it and its own loading state. */
  propose: (sourceKey: string) => Promise<DecoderProposalResponse>;
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
function CatalogueSection({ company, catalogue, ruleSets, canManage, busy,
                            problem, on, onCloseProblem }: {
  company: CompanyCatalogue;
  catalogue: CompanyCatalogueEntry;
  ruleSets: RuleSet[];
  canManage: boolean;
  busy: boolean;
  problem: string | null;
  on: CatalogueActions;
  onCloseProblem: () => void;
}) {
  const c = catalogue;
  const chip = chipFor(c);
  const report = c.report;
  const label = company.label || company.connection_id;
  const [renaming, setRenaming] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  // Families the rule set actually saw rows for, the parser's "(unresolved)"
  // census excluded — that count is shown as its own quarantine row.
  const families = report
    ? Object.entries(report.by_family).filter(([f]) => f !== "(unresolved)")
    : [];
  const newTokens = report ? Object.entries(report.new_tokens_top) : [];
  const builtFrom = c.built_from ?? [];
  // The rule sets the last build actually decoded through, distinct. Read off
  // the files rather than off the catalogue: there is no catalogue-level
  // decoder to read, and with two files that needed different rule sets there
  // is no single answer either.
  const decodedThrough = [...new Set(builtFrom.map((f) => f.rule_set ?? "—"))];
  // The files a build would refuse by name, as a person sees them: filenames,
  // not source keys.
  const awaiting = c.awaiting_decoding.map(
    (key) => c.sources.find((x) => x.source_key === key)?.filename ?? key);

  return (
    <Box sx={{ borderTop: 1, borderColor: "divider", pt: 1.5, mt: 1.5 }}>
      <HeadingRow
        name={nameOf(c)}
        level="catalogue"
        chip={
          <StatusChip label={chip.label} tone={chip.tone}
                      tip={c.exists
                        ? "This catalogue is built and on disk. It is part of what this company's quote lines resolve against."
                        : "This catalogue is not built, so nothing of this manufacturer's is in what the company resolves against — its part numbers answer UNKNOWN, not zero coverage, until it is."} />
        }
        meta={c.exists ? (
          <Typography variant="caption" color="text.secondary">
            {c.records} decoded records, built {since(c.built_at)}
          </Typography>
        ) : undefined}
        actions={canManage ? (
          <>
            {/* Build first, and the only contained button in the row: a screen
                where every control is filled has no primary action, per
                ui-standards §5. Off until every file has a saved config the
                engine can run — the server refuses such a build by name, a
                control that cannot succeed is the interface's version of the
                benign default, and the alert below says which file is waiting. */}
            <Button
              variant={c.exists ? "outlined" : "contained"} size="small"
              disabled={busy || c.sources.length === 0 || !c.decoding_ready}
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
        ) : undefined}
      />
      {busy && <LinearProgress sx={{ mb: 1 }} />}

      {problem && <ProblemAlert message={problem} onClose={onCloseProblem} />}
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
      {awaiting.length > 0 && (
        // Named, because "not built" is not useful advice to somebody whose
        // build would be refused by name — and because the reason it is
        // refused is the architecture rather than an error.
        <Alert severity="warning" sx={{ mb: 1.5 }}>
          <AlertTitle>
            Not decoded yet: {awaiting.join(", ")}
          </AlertTitle>
          Nothing is decoded through a default. Each file is analysed on its
          own — its columns, and every rule set this engine ships run over its
          first rows — and it is decoded only once somebody has read those
          counts and saved a decoding config for it. Open <b>Decoding</b> on
          {awaiting.length === 1 ? " that file" : " each of those files"} below.
        </Alert>
      )}

      <Box sx={{ mb: 1.5 }}>
        <CatalogSources
          catalogue={c} label={label} ruleSets={ruleSets}
          canManage={canManage} busy={busy}
          onUpload={on.upload} onSaveDecoding={on.saveDecoding}
          onAnalyze={on.analyze} onPropose={on.propose}
          onRemove={on.removeSource}
        />
      </Box>

      {/* Two panes, from the theme's own grid and breakpoints rather than the
          `.dp-split-2` class and its one-off 820px query — ui-standards §11 and
          §12. */}
      <Box sx={{
        display: "grid",
        gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" },
        gap: 2, alignItems: "start",
      }}>
        {/* A fact panel — a label and a value, a fixed handful of rows —
            which is the case ui-standards §3 keeps as a plain table. Ten rows
            at most, and the ten are set by what a build stamps rather than by
            how big this business is, which is the whole of that test. */}
        <Pane title="Files and provenance">
          <table className="facttable"
                 aria-label={`Files and provenance for ${label} · ${nameOf(c)}`}>
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
                  <Labelled tip="The rule sets the last build actually decoded these files through — each file its own, because each carries its own decoding config. Chosen from what the engine ships, never uploaded: a rule set is regexes the engine runs over every row of a file, and a pattern that backtracks catastrophically would be an outage a tenant could upload for themselves.">
                    Decoded through
                  </Labelled>
                </td>
                <td className="fv">
                  {builtFrom.length === 0 ? "not decoded yet"
                    : decodedThrough.join(", ")}
                  {/* Where two files needed different rule sets, one name for
                      the catalogue would be a wrong answer to "what decoded
                      this record" — so each file's own is named. */}
                  {decodedThrough.length > 1 && (
                    <div className="fsrc">
                      {builtFrom.map((f) => `${f.filename} → ${f.rule_set ?? "—"}`)
                                .join(" · ")}
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
                      <Labelled tip="The rule set's content hash. run_id derives from the input bytes plus this checksum, so this value — with the version — is what says which build answered a given resolution. Each file is decoded on its own, so where two files went through different rule sets there is no single checksum and each file's own is the answer.">
                        Ruleset checksum
                      </Labelled>
                    </td>
                    <td className="fv" style={MONO}>
                      {c.stamp.ruleset_checksum ?? "differs per file"}
                    </td>
                  </tr>
                  <tr>
                    <td>Run id<div className="fsrc">input bytes + ruleset</div></td>
                    <td className="fv" style={MONO}>
                      {c.stamp.run_id ?? "differs per file"}
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
                      <Labelled tip="Rows the rule set could not place in any family. Kept beside the catalogue rather than dropped — unknown means unknown.">
                        Quarantined
                      </Labelled>
                    </td>
                    <td className="fv">{c.quarantined}</td>
                  </tr>
                  {report && (
                    <tr>
                      <td>
                        <Labelled tip="Tokens the grammars did not recognise, captured verbatim by the parser. A growing list here is the signal a rule set needs new vocabulary.">
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
        </Pane>

        {/* Per-family census and parse rate, straight from the parser's run
            report. The row count is the rule set's declared family vocabulary
            — a property of its structure, not of the size of the business —
            which is the fixed-shape case that stays a plain table rather than
            a DataGrid.

            Only where there IS one report. Each file is decoded on its own, so
            a catalogue built from several has a report per file and none of
            its own; summing their censuses here would be the recomputation
            `catalog.run_parse` exists to make unnecessary. The per-file counts
            below stand in for it, and are a table for the same reason: their
            row count is the files one catalogue holds, capped by the server's
            own per-catalogue ceiling, and it is a fixed handful of label-and-
            value rows rather than a list anybody sorts. The files themselves
            — a list that does grow with the business — are the grid above. */}
        {c.exists && !report && builtFrom.length > 1 && (
          <Pane
            title="Rows by file"
            note={<>Each file&apos;s own counts, from its own decode — the
                    parser&apos;s numbers, not added up here.</>}
          >
            <table className="facttable"
                   aria-label={`Per-file parse counts for ${label} · ${nameOf(c)}`}>
              <tbody>
                {builtFrom.map((f) => (
                  <tr key={f.source_key}>
                    <td>
                      {f.filename}
                      <div className="fsrc">{f.rule_set ?? "—"}</div>
                    </td>
                    <td className="fv">
                      {f.records} classified
                      <div className="fsrc">
                        {f.quarantined} quarantined of {f.rows_read} rows read
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Pane>
        )}
        {c.exists && report && (
          <Pane
            title="Rows by family"
            note={<>Rows per family, with the grammar parse rate beside each —
                    the parser&apos;s own numbers, not recomputed here.</>}
          >
            <table className="facttable"
                   aria-label={`Per-family parse rates for ${label} · ${nameOf(c)}`}>
              <tbody>
                {families.map(([family, count]) => (
                  <tr key={family}>
                    <td>{family}</td>
                    <td className="fv">
                      {count}
                      <Box component="span" className="fsrc" sx={{ ml: 1 }}>
                        <PercentageValue value={report.parse_rates[family]} digits={1} />
                      </Box>
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
          </Pane>
        )}
      </Box>

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
   *  failure is attributed to it rather than to the page. Every one of these
   *  answers with the company's finished state, so the company is replaced
   *  from the response rather than re-fetching the whole list — including the
   *  re-analysis, which writes a fresh proposal beside one file. */
  async function run(id: string, action: () => Promise<CompanyCatalogue>) {
    setBusy(id);
    setProblem(null);
    try {
      replace(await action());
    } catch (e) {
      // The server names the specific cause — a column the file lacks, a rule
      // set the engine no longer ships, the files a build was refused over —
      // so it is shown verbatim rather than summarised away.
      setProblem({ id, message: (e as Error).message });
    } finally {
      setBusy(null);
    }
  }

  /** Every action one catalogue's section can take, bound to it here so the
   *  section never spells a URL or holds the token. */
  function actionsFor(c: CompanyCatalogueEntry): CatalogueActions {
    const t = session.token;
    const id = keyOf(c);
    const { connection_id: cid, catalogue_key: key } = c;
    return {
      build: () => run(id, () => papi.buildCompanyCatalog(t, cid, key)),
      upload: (file, sourceKey) =>
        run(id, () => papi.uploadCompanyCorpus(t, cid, key, file, sourceKey)),
      saveDecoding: (sourceKey, config) =>
        run(id, () => papi.saveSourceDecoding(t, cid, key, sourceKey, config)),
      analyze: (sourceKey) =>
        run(id, () => papi.analyzeSource(t, cid, key, sourceKey)),
      propose: (sourceKey) =>
        papi.proposeSourceDecoder(t, cid, key, sourceKey),
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
    <SectionHeader
      title="Decoded catalogue"
      sub="What each company resolves a part number against — one decoded catalogue per manufacturer it sells, merged — and which build of it answered."
    />
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

  const ruleSets = view.rule_sets;
  const addingTo = adding
    ? view.companies.find((c) => c.connection_id === adding) ?? null : null;

  return (
    <div>
      {head}

      {view.companies.length === 0 ? (
        // `EmptyState`, not a Paper with a sentence in it: nothing to show and
        // why is the question kit already answers, and this branch answered it
        // in its own words next to five that answer it in kit's.
        <EmptyState
          title="No company is connected"
          reason={<>A catalogue belongs to a company&apos;s books, so there is
                    nothing to build one against yet. Add a connection on{" "}
                    <b>Data &amp; connection</b>, and its catalogues can be
                    built here.</>}
        />
      ) : view.companies.map((c) => {
        const chip = unionChip(c);
        const companyActing = busy === c.connection_id;
        const atCeiling = c.catalogues.length >= view.max_catalogues;
        return (
          <Bp key={c.connection_id} sx={{ px: 2, pt: 1.5, pb: 2, mb: 1.5 }}>
            <HeadingRow
              name={c.label || c.connection_id}
              level="company"
              chip={
                <StatusChip label={chip.label} tone={chip.tone}
                            tip={c.union
                              ? "The union of every catalogue this company has built. Its quote lines resolve against this — never against another company's."
                              : "This company has built no catalogue. Its resolutions report UNKNOWN — not zero coverage — until one is built."} />
              }
              // Said beside the disabled control it explains, not under it.
              meta={view.can_manage && atCeiling ? (
                <Typography variant="caption" color="text.secondary">
                  at the ceiling of {view.max_catalogues}
                </Typography>
              ) : undefined}
              actions={view.can_manage ? (
                <Button size="small" variant="outlined"
                        disabled={companyActing || atCeiling}
                        onClick={() => setAdding(c.connection_id)}>
                  Add a catalogue
                </Button>
              ) : undefined}
            />
            {companyActing && <LinearProgress sx={{ mb: 1 }} />}

            {problem?.id === c.connection_id && (
              <ProblemAlert message={problem.message}
                            onClose={() => setProblem(null)} />
            )}
            {ruleSets.length === 0 && view.can_manage && (
              // The state the empty dropdown used to render as nothing at all.
              // `source.reason` distinguishes an uninitialised submodule from a
              // present engine with a missing corpus, which are different
              // fixes. Once per company, not per catalogue: it is a fact about
              // the deployment, and saying it three times says it less.
              <Alert severity="warning" sx={{ mb: 1.5 }}>
                <AlertTitle>No rule set is available to decode with</AlertTitle>
                A rule set is a decoder the engine ships — the rules that read
                how one manufacturer phrases a description. This deployment has
                none, so no file&apos;s decoding config can name one and nothing
                can be built until that is fixed; uploading a file here still
                works, and it decodes once a rule set is available and its
                config is saved.
                {view.source.reason ? ` ${view.source.reason}` : ""}
              </Alert>
            )}

            {c.union && <UnionFacts union={c.union} total={c.catalogues.length} />}

            {c.catalogues.length === 0 ? (
              <EmptyState
                title="No catalogue yet"
                reason={view.can_manage
                  ? "Add one per manufacturer this company sells — Kennametal, YG-1 — with Add a catalogue above. Each price list uploaded into one carries its own decoding config, and the company resolves against all of them. Until one is built, its resolutions answer UNKNOWN — not zero coverage."
                  : "Nothing has been defined for this company to decode. Its resolutions answer UNKNOWN — not zero coverage — until an owner adds a catalogue per manufacturer it sells, such as Kennametal or YG-1, and builds it."}
              />
            ) : c.catalogues.map((cat) => {
              const id = keyOf(cat);
              return (
                <CatalogueSection
                  key={id} company={c} catalogue={cat} ruleSets={ruleSets}
                  canManage={view.can_manage} busy={busy === id}
                  problem={problem?.id === id ? problem.message : null}
                  on={actionsFor(cat)}
                  onCloseProblem={() => setProblem(null)}
                />
              );
            })}
          </Bp>
        );
      })}

      {addingTo && (
        <AddCatalogueDialog
          busy={busy === addingTo.connection_id}
          onClose={() => setAdding(null)}
          onAdd={(name) => {
            setAdding(null);
            run(addingTo.connection_id, () => papi.createCompanyCatalogue(
              session.token, addingTo.connection_id, { name }));
          }} />
      )}

      {/* The standing terms of this screen, at the foot rather than repeated in
          every catalogue: what is read out of a file, what never is, and the
          two ceilings. `caption` is the ramp's secondary-metadata rung (§4),
          which is what this is — read once, then relied on. */}
      <Typography variant="caption" component="p" color="text.secondary"
                  sx={{ mt: 1, mb: 0, maxWidth: "90ch" }}>
        Nomenclature only — never price, cost or stock: a file&apos;s part
        number, description and grade are read, and every other column is left
        out of the catalogue rather than filtered out of it. CSV or Excel, up to
        {" "}{megabytes(view.max_corpus_bytes)} a file; up to{" "}
        {view.max_catalogues} catalogues a company.
        {!view.can_manage && " Uploading and building are owner actions."}
        <Tip text="A quote resolves against the union of the catalogues of the company it is raised from, and never against another company's — an answer from the wrong item master would carry a real provenance stamp for the wrong product." />
      </Typography>

      <CatalogLearning session={session} />
    </div>
  );
}
