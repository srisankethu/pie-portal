// The decoded product catalogue, one per connected company.
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
// different item masters. Each decodes its own export through its own pack, and
// a quote resolves against the catalogue of the company it was raised from —
// never against another's. A company that has uploaded nothing resolves
// nothing, which the chip says in those words.
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
// The row count here is the number of companies a business keeps, which is a
// property of its structure rather than of its size — so these are per-company
// Paper surfaces like `ConnectionsPanel`'s, not a grid. The *files* inside one
// company are a grid (`CatalogSources`), for the opposite reason: how many
// exports a business keeps grows with the business.
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
// this company's own files and reports the parser's counts for each. The counts
// are the parser's; nothing here computes a score, because a fit number this
// screen invented would be a second parse-rate calculation next to the one
// `catalog.run_parse` already refuses to duplicate.

import { useCallback, useEffect, useState } from "react";
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import LinearProgress from "@mui/material/LinearProgress";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";

import { papi } from "./api";
import { CatalogSources, fileSize, megabytes } from "./CatalogSources";
import { ErrorState, LoadingState, PercentageValue, StatusChip } from "./kit";
import type { CompanyCatalogue, CompanyCatalogues as View, PackFit,
              PlatformSession } from "./types";
import { Bp, Labelled, Tip } from "./ui";
import { formatDateTime, since } from "../when";

/** The one state word for a company, from the facts the server sends.
 *
 *  Ordered by what has to be fixed first: without a pack or a corpus there is
 *  nothing to build, so those are named ahead of NOT BUILT rather than
 *  collapsed into it. "You have not built it" is not useful advice to someone
 *  who has nothing to build it from. */
function chipFor(c: CompanyCatalogue): { label: string; tone: "good" | "warn" | "bad" | "neutral" } {
  if (!c.pack_id) return { label: "NO PACK CHOSEN", tone: "neutral" };
  if (!c.pack_resolved) return { label: "PACK NOT FOUND", tone: "bad" };
  if (c.sources.length === 0) return { label: "NO EXPORT", tone: "neutral" };
  if (c.built_but_missing_on_disk) return { label: "NEEDS REBUILD", tone: "warn" };
  if (!c.exists) return { label: "NOT BUILT", tone: "warn" };
  if (c.stale) return { label: "OUT OF DATE", tone: "warn" };
  return { label: "READY", tone: "good" };
}

/** Every shipped pack, tried against a sample of one company's own files.
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

export function CatalogScreen({ session }: { session: PlatformSession }) {
  const [view, setView] = useState<View | null>(null);
  const [error, setError] = useState<string | null>(null);
  /** Which company is mid-action, so only its own controls are disabled — one
   *  company building must not freeze the others. */
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<{ id: string; message: string } | null>(null);
  /** The trial's result, for the one company it was asked about. Not stored per
   *  company: it is a sample measured a moment ago against files that can
   *  change, so keeping several around would show two answers of different
   *  ages side by side. */
  const [fit, setFit] = useState<{ id: string; result: PackFit } | null>(null);

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

  /** Swap one company's row for the state the server just returned. */
  function replace(updated: CompanyCatalogue) {
    setView((v) => (v ? {
      ...v,
      companies: v.companies.map((c) =>
        c.connection_id === updated.connection_id ? updated : c),
    } : v));
  }

  /** One request against one company: only that company's controls go quiet,
   *  and a failure is attributed to it rather than to the page.
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
      // the engine no longer ships, a file that was never uploaded — so it is
      // shown verbatim rather than summarised away.
      setProblem({ id, message: (e as Error).message });
    } finally {
      setBusy(null);
    }
  }

  /** An action that returns the company's finished state, so the row is
   *  replaced from the response rather than re-fetching the whole list. */
  function run(id: string, action: () => Promise<CompanyCatalogue>) {
    return acting(id, action, replace);
  }

  /** Run the trial for one company. Shares `busy` with the actions, because it
   *  is a parse per pack and a build queued behind it would sit on the lock. */
  function tryPacks(id: string) {
    setFit(null);
    return acting(id, () => papi.companyPackFit(session.token, id),
                  (result) => setFit({ id, result }));
  }

  // The heading is part of every branch, not only the loaded one: as its own
  // address this screen can be opened cold, and a page that renders a bare
  // error box with no title does not say which screen failed.
  const head = (
    <div className="dp-head">
      <h1>Decoded catalogue</h1>
      <p>
        What each company&apos;s resolution reads a part number against — and
        which build of it answered.
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
        <LoadingState rows={1} height={90} label="Reading each company's catalogue…" />
      </div>
    );
  }

  return (
    <div>
      {head}

      {view.companies.length === 0 ? (
        <Bp style={{ padding: "14px" }}>
          <p className="st-help" style={{ margin: 0 }}>
            No companies are connected yet. Add one on <b>Data &amp; connection</b>,
            then its catalogue can be built here.
          </p>
        </Bp>
      ) : view.companies.map((c) => {
        const chip = chipFor(c);
        const packs = view.packs;
        const acting = busy === c.connection_id;
        const report = c.report;
        // Families the pack actually saw rows for, the parser's "(unresolved)"
        // census excluded — that count is shown as its own quarantine row.
        const families = report
          ? Object.entries(report.by_family).filter(([f]) => f !== "(unresolved)")
          : [];
        const newTokens = report ? Object.entries(report.new_tokens_top) : [];
        return (
          <Bp key={c.connection_id} style={{ padding: "10px 14px 14px", marginBottom: 10 }}>
            <div style={{ display: "flex", gap: 8, alignItems: "center",
                          flexWrap: "wrap", marginBottom: 8 }}>
              <b>{c.label || c.connection_id}</b>
              <StatusChip label={chip.label} tone={chip.tone}
                          tip={c.exists
                            ? "A catalogue is on disk. This company's quote lines resolve against it."
                            : "This company has no catalogue. Its resolutions report UNKNOWN — not zero coverage — until one is built."} />
              {c.exists && (
                <span className="st-help">
                  {c.records} decoded records, built {since(c.built_at)}
                </span>
              )}
              <Box sx={{ flex: 1 }} />
              {view.can_manage && (
                <>
                  {/* Disabled where there is nothing to choose, and the reason
                      is stated under the control rather than left to an empty
                      menu. `helperText` on a disabled field is the one place a
                      person is already looking when a dropdown does nothing. */}
                  <TextField
                    select size="small" label="Pack"
                    sx={{ minWidth: 160 }}
                    value={packs.some((p) => p.id === c.pack_id) ? c.pack_id : ""}
                    disabled={acting || packs.length === 0}
                    helperText={packs.length === 0 ? "none available" : undefined}
                    onChange={(e) => run(c.connection_id, () =>
                      papi.setCompanyPack(session.token, c.connection_id, e.target.value))}
                  >
                    {packs.map((p) => (
                      <MenuItem key={p.id} value={p.id}>{p.id}</MenuItem>
                    ))}
                  </TextField>
                  {/* Offered whenever there is a pack and a file, not only
                      where there are several packs to choose between. With one
                      shipped pack the question is not "which of these" but
                      "does this one read my file at all" — and that is the
                      more important of the two, because the answer decides
                      whether a pack has to be written. */}
                  {packs.length > 0 && c.sources.length > 0 && (
                    <Button size="small" disabled={acting}
                            onClick={() => tryPacks(c.connection_id)}>
                      {packs.length > 1 ? "Which pack fits?" : "Try this pack"}
                    </Button>
                  )}
                  <Button
                    variant={c.exists ? "outlined" : "contained"} size="small"
                    disabled={acting || c.sources.length === 0 || !c.pack_resolved}
                    onClick={() => run(c.connection_id, () =>
                      papi.buildCompanyCatalog(session.token, c.connection_id))}
                  >
                    {acting ? "Working…" : c.exists ? "Rebuild" : "Build"}
                  </Button>
                </>
              )}
            </div>
            {acting && <LinearProgress sx={{ mb: 1 }} />}

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
              // present engine with a missing corpus, which are different fixes.
              <Alert severity="warning" sx={{ mb: 1.5 }}>
                <AlertTitle>No pack is available to decode with</AlertTitle>
                A pack is the decoder the engine ships — the rules that read a
                part number and a description. This deployment has none, so
                nothing can be built until that is fixed; uploading a file here
                still works and it will decode once a pack is available.
                {view.source.reason ? ` ${view.source.reason}` : ""}
              </Alert>
            )}
            {c.stale && (
              <Alert severity="warning" sx={{ mb: 1.5 }}>
                This catalogue was built from a different set of files than the
                ones on record now — one has been added, replaced or removed.
                It is still a real catalogue with a real stamp; it is just not
                built from what this company now holds. Rebuild to decode the
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
            {fit?.id === c.connection_id && (
              <PackFitPanel fit={fit.result} chosen={c.pack_id}
                            onClose={() => setFit(null)}
                            onChoose={(packId) => {
                              setFit(null);
                              run(c.connection_id, () => papi.setCompanyPack(
                                session.token, c.connection_id, packId));
                            }} />
            )}

            <Box sx={{ mb: 1.5 }}>
              <CatalogSources
                company={c} canManage={view.can_manage} busy={acting}
                onUpload={(file, key) => run(c.connection_id, () =>
                  papi.uploadCompanyCorpus(session.token, c.connection_id, file, key))}
                onMap={(key, mapping) => run(c.connection_id, () =>
                  papi.setSourceMapping(session.token, c.connection_id, key, mapping))}
                onRemove={(key) => run(c.connection_id, () =>
                  papi.removeCompanySource(session.token, c.connection_id, key))}
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
                        <Labelled tip="The item-master export this company's catalogue is decoded from. Stored as a record rather than a file, so it survives a redeploy and the catalogue can always be rebuilt from it.">
                          Item-master export
                        </Labelled>
                      </td>
                      {/* The set rather than one filename: the grid below names
                          the files, so repeating the newest one here would be
                          the same fact stated twice and stale half the time. */}
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
                        <Labelled tip="The decoder: the rules that read how this company's export phrases a description. Chosen from what the engine ships — never uploaded, because a pack is regexes the engine runs over every row of a file, and a pattern that backtracks catastrophically would be an outage a tenant could upload for themselves. Which one fits is answerable: 'Which pack fits?' runs each of them over a sample of these files.">
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

              {/* Per-family census and parse rate, straight from the parser's
                  run report. The row count is the pack's declared family
                  vocabulary — a property of the pack's structure, not of the
                  size of the business — which is the fixed-shape case that
                  stays a plain table rather than a DataGrid. */}
              {c.exists && report && (
                <div>
                  <table className="facttable"
                         aria-label={`Per-family parse rates for ${c.label || c.connection_id}`}>
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
          </Bp>
        );
      })}

      <p className="st-help" style={{ marginTop: 8 }}>
        Nomenclature only — never price, cost or stock: a file&apos;s part
        number, description and grade are read, and every other column is left
        out of the catalogue rather than filtered out of it. CSV or Excel, up to
        {" "}{megabytes(view.max_corpus_bytes)} a file.
        {!view.can_manage && " Uploading and building are owner actions."}
        <Tip text="A quote resolves against the catalogue of the company it is raised from, and never against another company's — an answer from the wrong item master would carry a real provenance stamp for the wrong product." />
      </p>
    </div>
  );
}
