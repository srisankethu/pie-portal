// The decoded product catalogue: does one exist, what built it, and rebuild.
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
// Every number rendered comes from the parser's own run report, stored beside
// the catalogue at build time and served verbatim. Nothing is recomputed here
// or on the server — a second parse-rate calculation is exactly the drift
// CLAUDE.md §2 warns about.
//
// NOT BUILT is its own state, never 0%. A missing catalogue says nothing
// about coverage — that distinction (`pie_service.catalog_available`) is
// load-bearing in the coverage reports, and this screen keeps it.

import { useCallback, useEffect, useState } from "react";
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import LinearProgress from "@mui/material/LinearProgress";

import { papi } from "./api";
import { ErrorState, LoadingState, PercentageValue, StatusChip } from "./kit";
import type { CatalogStatus, PlatformSession } from "./types";
import { Bp, Labelled, Tip } from "./ui";
import { formatDateTime, since } from "../when";

/** The one state word the chip carries, from the two server facts. */
function chipFor(c: CatalogStatus): { label: string; tone: "good" | "warn" | "bad" } {
  if (c.exists) return { label: "READY", tone: "good" };
  if (!c.source.available) return { label: "SOURCE MISSING", tone: "bad" };
  return { label: "NOT BUILT", tone: "warn" };
}

export function CatalogScreen({ session }: { session: PlatformSession }) {
  const [status, setStatus] = useState<CatalogStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [building, setBuilding] = useState(false);
  const [buildError, setBuildError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setStatus(await papi.catalogStatus(session.token));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [session.token]);

  useEffect(() => {
    load();
  }, [load]);

  async function build() {
    setBuilding(true);
    setBuildError(null);
    try {
      // Synchronous on the server (~2s for the full corpus), so the response
      // IS the finished state — no job to poll.
      setStatus(await papi.buildCatalog(session.token));
    } catch (e) {
      setBuildError((e as Error).message);
    } finally {
      setBuilding(false);
    }
  }

  // The heading is part of every branch, not only the loaded one: as its own
  // address this screen can be opened cold, and a page that renders a bare
  // error box with no title does not say which screen failed.
  const head = (
    <div className="dp-head">
      <h1>Decoded catalogue</h1>
      <p>
        What resolution reads a part number against — and which build of it
        answered.
      </p>
    </div>
  );

  if (error) {
    return (
      <div>
        {head}
        <ErrorState title="Could not read the catalogue state" error={error}
                    onRetry={load} />
      </div>
    );
  }
  if (!status) {
    return (
      <div>
        {head}
        <LoadingState rows={1} height={90} label="Reading the catalogue state…" />
      </div>
    );
  }

  const chip = chipFor(status);
  const report = status.report;
  // Families the pack actually saw rows for, the parser's "(unresolved)"
  // census excluded — that count is shown as its own quarantine row.
  const families = report
    ? Object.entries(report.by_family).filter(([f]) => f !== "(unresolved)")
    : [];
  const newTokens = report ? Object.entries(report.new_tokens_top) : [];
  // The file's checksum vs what this process resolves with. They differ only
  // when the file changed under a running process that has not reloaded —
  // worth a sentence, because then the screen and the resolutions disagree.
  const loadedChecksum = status.loaded.ruleset_checksum;
  const staleLoad = Boolean(
    status.exists && status.loaded.index_loaded && loadedChecksum
    && status.stamp.ruleset_checksum
    && loadedChecksum !== status.stamp.ruleset_checksum);

  return (
    <div>
      {head}
      <Bp style={{ padding: "8px 14px 14px" }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center",
                      flexWrap: "wrap", margin: "8px 0" }}>
          <StatusChip label={chip.label} tone={chip.tone}
                      tip={status.exists
                        ? "A catalogue is on disk. Resolutions run against it."
                        : "No catalogue exists. Resolution surfaces report UNKNOWN — not zero coverage — until one is built."} />
          {status.exists && (
            <span className="st-help">
              {status.records} decoded records, built {since(status.built_at)}
            </span>
          )}
          <Box sx={{ flex: 1 }} />
          {status.can_rebuild && status.source.available && (
            <Button variant={status.exists ? "outlined" : "contained"} size="small"
                    onClick={build} disabled={building}>
              {building ? "Building…"
                : status.exists ? "Rebuild catalogue" : "Build catalogue"}
            </Button>
          )}
          <Tip text="One catalogue for the whole deployment: every organization on this server resolves against it, which is why rebuilding is an owner action." />
        </div>
        {building && <LinearProgress sx={{ mb: 1 }} />}

        {/* The server names the specific failure — corpus missing, submodule
            uninitialised, disk full, a parse error — so it is shown verbatim
            rather than summarised into "something went wrong". */}
        {buildError && (
          <Alert severity="error" sx={{ mb: 1.5 }}>
            <AlertTitle>The build failed</AlertTitle>
            {buildError}
          </Alert>
        )}
        {!status.source.available && (
          <Alert severity={status.exists ? "warning" : "error"} sx={{ mb: 1.5 }}>
            <AlertTitle>
              {status.exists
                ? "The catalogue cannot be rebuilt here"
                : "A catalogue cannot be built here"}
            </AlertTitle>
            {status.source.reason}
          </Alert>
        )}
        {staleLoad && (
          <Alert severity="warning" sx={{ mb: 1.5 }}>
            The running engine loaded a different build (ruleset {loadedChecksum})
            than the file on disk. Resolutions are stamped with the loaded one
            until the engine reloads.
          </Alert>
        )}

        {!status.exists ? (
          <p className="st-help">
            {status.source.available && (
              <>
                Item identity lookups and RFQ line resolution answer UNKNOWN until
                a catalogue is built
                {status.auto_build
                  ? " — it will also build itself on the first resolution that needs it."
                  : "."}
                {!status.can_rebuild && " Building it is an owner action."}
              </>
            )}
          </p>
        ) : (
          <div className="dp-split-2">
            {/* A fact panel — a label and a value, a fixed handful of rows —
                which is the case ui-standards §3 keeps as a plain table. */}
            <div>
              <table className="facttable">
                <tbody>
                  <tr>
                    <td>
                      <Labelled tip="The manufacturer nomenclature layer that decoded the rows, and the organisation layer that routed them. Stamped on every record.">
                        Pack
                      </Labelled>
                    </td>
                    <td className="fv">
                      {status.stamp.pack_id} v{status.stamp.pack_version}
                      <div className="fsrc">
                        org layer {status.stamp.org_id} v{status.stamp.org_version}
                      </div>
                    </td>
                  </tr>
                  <tr>
                    <td>
                      <Labelled tip="The pack's content hash. run_id derives from the input bytes plus this checksum, so this value — with the pack version — is what says which catalogue answered a given resolution.">
                        Ruleset checksum
                      </Labelled>
                    </td>
                    <td className="fv" style={{ fontFamily: "var(--font-mono, monospace)" }}>
                      {status.stamp.ruleset_checksum}
                    </td>
                  </tr>
                  <tr>
                    <td>Run id<div className="fsrc">input bytes + ruleset</div></td>
                    <td className="fv" style={{ fontFamily: "var(--font-mono, monospace)" }}>
                      {status.stamp.run_id}
                    </td>
                  </tr>
                  <tr>
                    <td>Built</td>
                    <td className="fv">
                      {formatDateTime(status.built_at)}
                      {status.build?.duration_s != null && (
                        <div className="fsrc">took {status.build.duration_s}s</div>
                      )}
                    </td>
                  </tr>
                  {report && (
                    <>
                      <tr><td>Rows read</td><td className="fv">{report.total}</td></tr>
                      <tr><td>Classified</td><td className="fv">{status.build?.emitted ?? status.records}</td></tr>
                      <tr>
                        <td>
                          <Labelled tip="Rows the pack could not place in any family. Kept verbatim next to the catalogue rather than dropped — unknown means unknown.">
                            Quarantined
                          </Labelled>
                        </td>
                        <td className="fv">{status.build?.quarantined ?? report.unresolved_family}</td>
                      </tr>
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
                    </>
                  )}
                  <tr>
                    <td>Engine</td>
                    <td className="fv">
                      v{status.stamp.engine_version}
                      <div className="fsrc">schema v{status.stamp.schema_version}</div>
                    </td>
                  </tr>
                </tbody>
              </table>
              {status.report_missing && (
                <Alert severity="info" sx={{ mt: 1.5 }}>{status.report_missing}</Alert>
              )}
            </div>

            {/* Per-family census and parse rate, straight from the parser's
                run report. The row count is the pack's declared family
                vocabulary — a property of the pack's structure, not of the
                size of the business — which is the fixed-shape case that
                stays a plain table rather than a DataGrid. */}
            {report && (
              <div>
                <table className="facttable" aria-label="Per-family parse rates">
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
        )}

        <p className="st-help" style={{ marginTop: 10, marginBottom: 0 }}>
          Deployment-wide: one decoded catalogue serves every organization on
          this server. It carries nomenclature only — never price, cost or stock.
        </p>
      </Bp>
    </div>
  );
}
