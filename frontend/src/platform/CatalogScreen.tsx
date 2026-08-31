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
// Paper surfaces like `ConnectionsPanel`'s, not a grid.

import { useCallback, useEffect, useRef, useState } from "react";
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import LinearProgress from "@mui/material/LinearProgress";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";

import { papi } from "./api";
import { ErrorState, LoadingState, PercentageValue, StatusChip } from "./kit";
import type { CompanyCatalogue, CompanyCatalogues as View, PlatformSession } from "./types";
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
  if (!c.corpus) return { label: "NO EXPORT", tone: "neutral" };
  if (c.built_but_missing_on_disk) return { label: "NEEDS REBUILD", tone: "warn" };
  if (!c.exists) return { label: "NOT BUILT", tone: "warn" };
  if (c.stale) return { label: "OUT OF DATE", tone: "warn" };
  return { label: "READY", tone: "good" };
}

function megabytes(bytes: number): string {
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function CatalogScreen({ session }: { session: PlatformSession }) {
  const [view, setView] = useState<View | null>(null);
  const [error, setError] = useState<string | null>(null);
  /** Which company is mid-action, so only its own controls are disabled — one
   *  company building must not freeze the others. */
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<{ id: string; message: string } | null>(null);
  const fileInputs = useRef<Record<string, HTMLInputElement | null>>({});

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

  /** Every action returns the company's finished state, so the row is replaced
   *  from the response rather than re-fetching the whole list. */
  function replace(updated: CompanyCatalogue) {
    setView((v) => (v ? {
      ...v,
      companies: v.companies.map((c) =>
        c.connection_id === updated.connection_id ? updated : c),
    } : v));
  }

  async function run(id: string, action: () => Promise<CompanyCatalogue>) {
    setBusy(id);
    setProblem(null);
    try {
      replace(await action());
    } catch (e) {
      // The server names the specific cause — a column the export lacks, a
      // pack the engine no longer ships, a corpus that was never uploaded —
      // so it is shown verbatim rather than summarised away.
      setProblem({ id, message: (e as Error).message });
    } finally {
      setBusy(null);
    }
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
                  <TextField
                    select size="small" label="Pack"
                    sx={{ minWidth: 160 }}
                    value={c.pack_id ?? ""}
                    disabled={acting}
                    onChange={(e) => run(c.connection_id, () =>
                      papi.setCompanyPack(session.token, c.connection_id, e.target.value))}
                  >
                    {view.packs.map((p) => (
                      <MenuItem key={p.id} value={p.id}>{p.id}</MenuItem>
                    ))}
                  </TextField>
                  <input
                    ref={(el) => { fileInputs.current[c.connection_id] = el; }}
                    type="file" accept=".csv,text/csv" hidden
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      e.target.value = "";   // so the same file can be re-picked
                      if (file) {
                        run(c.connection_id, () => papi.uploadCompanyCorpus(
                          session.token, c.connection_id, file));
                      }
                    }}
                  />
                  <Button variant="outlined" size="small" disabled={acting}
                          onClick={() => fileInputs.current[c.connection_id]?.click()}>
                    {c.corpus ? "Replace export" : "Upload export"}
                  </Button>
                  <Button
                    variant={c.exists ? "outlined" : "contained"} size="small"
                    disabled={acting || !c.corpus || !c.pack_resolved}
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
            {c.stale && (
              <Alert severity="warning" sx={{ mb: 1.5 }}>
                A newer export has been uploaded since this catalogue was built.
                It is still a real catalogue — it is just not built from what
                this company last sent. Rebuild to decode the new one.
              </Alert>
            )}
            {c.built_but_missing_on_disk && (
              <Alert severity="warning" sx={{ mb: 1.5 }}>
                This catalogue was built but its decoded file is not on disk —
                usually a redeploy. The export it was built from is stored, so
                rebuilding restores it exactly.
              </Alert>
            )}
            {!c.exists && !c.corpus && (
              <p className="st-help">
                Item identity lookups and this company&apos;s RFQ line
                resolution answer UNKNOWN until an export is uploaded and
                decoded.
                {!view.can_manage && " Uploading and building are owner actions."}
              </p>
            )}

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
                      <td className="fv">
                        {c.corpus ? (
                          <>
                            {c.corpus.filename}
                            <div className="fsrc">
                              {megabytes(c.corpus.size_bytes)} · uploaded{" "}
                              {formatDateTime(c.corpus.uploaded_at)}
                            </div>
                          </>
                        ) : "none uploaded"}
                      </td>
                    </tr>
                    <tr>
                      <td>
                        <Labelled tip="The org-layer pack that says how this company's export phrases a description. Chosen from what the engine ships — never uploaded, because a pack is regexes the engine runs over every row.">
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
                        <tr><td>Rows read</td><td className="fv">{c.rows_read}</td></tr>
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
        Nomenclature only — never price, cost or stock. An export may weigh up
        to {megabytes(view.max_corpus_bytes)}.
        {!view.can_manage && " Uploading and building are owner actions."}
        <Tip text="A quote resolves against the catalogue of the company it is raised from, and never against another company's — an answer from the wrong item master would carry a real provenance stamp for the wrong product." />
      </p>
    </div>
  );
}
