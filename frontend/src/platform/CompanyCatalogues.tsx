// Each connected company's own decoded catalogue.
//
// One organization can read three Zoho books, and those three companies have
// three different item masters. Until now they shared one deployment-wide
// catalogue, which is honest only while every company sells the same
// manufacturer's product through the same phrasing. This is the setup path for
// the alternative: a company uploads its own export, picks the pack that
// decodes it, and builds.
//
// **Nothing resolves against these yet.** The deployment-wide catalogue above
// still answers every quote line. The cutover — a quote naming its company, the
// resolution API gaining a refusal, and the default being removed — is a
// separate change, so this one cannot regress a live deployment.
// `docs/per-company-catalogues.md` §8 has the sequencing.
//
// The row count here is the number of Zoho books a business keeps, which is a
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
import { ErrorState, LoadingState, StatusChip } from "./kit";
import type { CompanyCatalogue, CompanyCatalogues as View, PlatformSession } from "./types";
import { Bp, Labelled } from "./ui";
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

export function CompanyCatalogues({ session }: { session: PlatformSession }) {
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

  if (error) {
    return (
      <Box sx={{ mt: 2 }}>
        <ErrorState title="Could not read the per-company catalogues"
                    error={error} onRetry={load} />
      </Box>
    );
  }
  if (!view) {
    return <LoadingState rows={1} height={80} label="Reading each company's catalogue…" />;
  }

  return (
    <>
      <div className="section-h">
        <Labelled tip="Each connected company decodes its own item-master export through its own pack. Nothing resolves against these yet — the deployment-wide catalogue above still answers every quote line.">
          Per-company catalogues
        </Labelled>
      </div>

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
        return (
          <Bp key={c.connection_id} style={{ padding: "10px 14px 14px", marginBottom: 10 }}>
            <div style={{ display: "flex", gap: 8, alignItems: "center",
                          flexWrap: "wrap", marginBottom: 8 }}>
              <b>{c.label || c.connection_id}</b>
              <StatusChip label={chip.label} tone={chip.tone} />
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

            {/* A fact panel: a label and a value, a fixed handful of rows. */}
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
                  </td>
                </tr>
                {c.exists && (
                  <>
                    <tr>
                      <td>
                        <Labelled tip="run_id derives from the input bytes plus the ruleset checksum, so this is what says which build answered a given resolution.">
                          Ruleset checksum
                        </Labelled>
                      </td>
                      <td className="fv" style={{ fontFamily: "var(--font-mono, monospace)" }}>
                        {c.stamp.ruleset_checksum}
                      </td>
                    </tr>
                    <tr><td>Rows read</td><td className="fv">{c.rows_read}</td></tr>
                    <tr>
                      <td>
                        <Labelled tip="Rows the pack could not place in any family. Kept beside the catalogue rather than dropped — unknown means unknown.">
                          Quarantined
                        </Labelled>
                      </td>
                      <td className="fv">{c.quarantined}</td>
                    </tr>
                  </>
                )}
              </tbody>
            </table>
          </Bp>
        );
      })}

      <p className="st-help" style={{ marginTop: 8 }}>
        Nomenclature only — never price, cost or stock. An export may weigh up
        to {megabytes(view.max_corpus_bytes)}.
        {!view.can_manage && " Uploading and building are owner actions."}
      </p>
    </>
  );
}
