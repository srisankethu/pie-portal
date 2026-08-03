import { useCallback, useEffect, useState } from "react";
import { papi } from "./api";
import { ConnectionsPanel } from "./ConnectionsPanel";
import type { DataStatus, PlatformSession, ZohoCredential } from "./types";
import { Bp, Labelled, Tip } from "./ui";

/**
 * Data & connection.
 *
 * Answers one question without requiring a terminal: is this showing the
 * company's real books, or sample data? The state is stated in words rather
 * than left to be inferred from whether numbers look plausible — a demo
 * dataset and a live one are indistinguishable by eye, which is exactly how
 * someone ends up trusting a decision built on fixtures.
 *
 * The companies themselves live in ``ConnectionsPanel``: an organization can
 * read as many Zoho books as the business keeps, and per-company health does
 * not fit in a single status badge.
 */

const RESULT_UI: Record<string, string> = {
  OK: "Succeeded",
  PARTIAL: "Stopped part-way",
  FAILED: "Failed",
};

/** A default worth offering rather than a default worth hiding: eighteen months
 *  gives the detectors a full recent window, a full comparison window and room
 *  above the six-month history floor. The operator can move it either way. */
function defaultSince(): string {
  const d = new Date();
  d.setMonth(d.getMonth() - 18);
  d.setDate(1);
  return d.toISOString().slice(0, 10);
}

function when(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const mins = Math.round((Date.now() - d.getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)} h ago`;
  return d.toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
}

export function DataScreen({ session, onSynced }: { session: PlatformSession; onSynced: () => void }) {
  const [status, setStatus] = useState<DataStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncingId, setSyncingId] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [since, setSince] = useState<string>(defaultSince());
  const [full, setFull] = useState(false);
  // Grants on file, shown here because rotation is a property of the sign-in
  // rather than of any one company: one rotation covers every company it reaches.
  const [credentials, setCredentials] = useState<ZohoCredential[]>([]);
  const [rotateToken, setRotateToken] = useState("");
  const [credMsg, setCredMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    setChecking(true);
    setError(null);
    try {
      setStatus(await papi.dataStatus(session.token));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setChecking(false);
    }
  }, [session.token]);

  useEffect(() => {
    load();
  }, [load]);

  const loadCredentials = useCallback(async () => {
    if (!status?.can_manage_connection) return;
    try {
      setCredentials((await papi.listCredentials(session.token)).credentials);
    } catch {
      /* an owner without credentials yet is the normal first-run state */
    }
  }, [session.token, status?.can_manage_connection]);

  useEffect(() => {
    loadCredentials();
  }, [loadCredentials]);

  /** One pull. `connectionId` names a company; omitted means every enabled one.
   *
   *  The window is passed in rather than read from a single shared box: each
   *  company's card carries its own date, because one entity may have four
   *  years of books worth reading and another four months, and one date for
   *  all of them either over-reads or under-reads at least one. */
  async function sync(connectionId?: string, fromDate?: string, reread?: boolean) {
    setSyncing(true);
    setSyncingId(connectionId ?? null);
    setResult(null);
    setError(null);
    try {
      const r = await papi.runSync(session.token, {
        since: (fromDate ?? since) || undefined,
        full: reread ?? full,
        connection_id: connectionId,
      });
      const run = r.run;
      const pulled =
        `Pulled ${run.sales_txns} sales lines and ${run.cost_records} cost records` +
        (run.documents_resumed ? ` (${run.documents_resumed} already held, not re-read)` : "");
      const demoRemoved = r.demo_data_removed;
      const demoNote = demoRemoved
        ? ` Removed the leftover sample data (${demoRemoved.customers ?? 0} customers, ` +
          `${demoRemoved.decisions ?? 0} decisions) now that real data has arrived.`
        : "";
      setResult(
        (run.status === "OK"
          ? `${pulled} — ${run.signals_emitted} signals, ${run.decisions_created} new decisions.`
          : run.status === "PARTIAL"
            ? `${pulled}, then stopped. Nothing was lost — run it again and it will carry on ` +
              `from here. Reason: ${run.error}`
            : `Sync failed before anything was read: ${run.error}`) + demoNote,
      );
      setFull(false);
      await load();
      onSynced();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSyncing(false);
      setSyncingId(null);
    }
  }

  async function doRotate(credentialId: string) {
    setCredMsg(null);
    try {
      await papi.rotateCredential(session.token, credentialId, rotateToken.trim());
      setRotateToken("");
      setCredMsg("Rotated. Every company connected through this sign-in now uses the new token.");
      await loadCredentials();
      await load();
    } catch (err) {
      setCredMsg((err as Error).message);
    }
  }

  const s = status?.last_sync;
  const canSync = Boolean(status?.can_sync);

  return (
    <div>
      <div className="dp-head">
        <h1>Data &amp; connection</h1>
        <p>Where the numbers come from, and when they last arrived.</p>
      </div>

      {error && (
        <div className="state-panel" style={{ marginBottom: 14 }}>
          <div className="state-mark">Could not read the connection status</div>
          <p style={{ margin: 0, fontSize: 13.5 }}>{error}</p>
        </div>
      )}

      <ConnectionsPanel
        session={session}
        canSync={canSync}
        onSync={(id, from, reread) => sync(id, from, reread)}
        syncingId={syncingId}
      />

      {/* ── what to pull ── */}
      <Bp className="st-section" style={{ marginTop: 16 }}>
        <h3>
          <Labelled tip="One pull reads invoices and bills, rebuilds the Customer × Item metrics from what landed, then runs the detectors. Nothing here is generated — every figure comes from a document Zoho returned.">
            Pull the books
          </Labelled>
        </h3>
        {!canSync ? (
          <p className="st-help">Syncing is a manager or owner action.</p>
        ) : (
          <>
            <div className="sync-opts">
              <label htmlFor="sync-since">
                <Labelled tip="Every invoice and bill after this date is fetched individually, so an earlier date means a longer pull. The detectors compare the last 90 days against the 90 before that and need six months of history before they will call a decline — eighteen months covers all of it with room to spare.">
                  Read the books from
                </Labelled>
              </label>
              <input
                id="sync-since"
                type="date"
                className="input"
                value={since}
                max={new Date().toISOString().slice(0, 10)}
                onChange={(e) => setSince(e.target.value)}
              />
              <label className="sync-check">
                <input type="checkbox" checked={full} onChange={(e) => setFull(e.target.checked)} />
                Re-read everything, including documents already held
                <Tip text="Normally a pull skips documents it already holds, which is what makes a repeat run fast. Tick this after granting a scope that was missing — the rows are there, but the fields that scope unlocks are not." />
              </label>
            </div>

            <div style={{ display: "flex", gap: 8, marginTop: 14, flexWrap: "wrap", alignItems: "center" }}>
              <button className="btn btn-secondary btn-sm" onClick={load} disabled={checking}>
                {checking ? "Checking…" : "Refresh status"}
              </button>
              <button className="btn btn-primary btn-sm" onClick={() => sync()} disabled={syncing}>
                {syncing && !syncingId ? "Syncing…" : "Sync every company"}
              </button>
              <Tip text="Runs one pull per enabled company, in turn. To pull just one, use the button on its card above." />
            </div>
          </>
        )}
        {result && <div className="conn-result">{result}</div>}
      </Bp>

      {/* ── rotation is a property of the sign-in, not of a company ── */}
      {status?.can_manage_connection && credentials.filter((cr) => cr.is_owner).map((cr) => (
        <Bp className="st-section" key={cr.credential_id}>
          <h3>
            <Labelled tip="A refresh token belongs to a Zoho user, not a company. Rotating it here replaces it once for every company connected through it — which is the whole reason connections and sign-ins are separate things.">
              Rotate {cr.label || "this sign-in"}
            </Labelled>
          </h3>
          <p className="st-help">
            Covers {cr.used_by.length}{" "}
            {cr.used_by.length === 1 ? "company" : "companies"}
            {cr.rotated_at && <> · last rotated {cr.rotated_at.slice(0, 10)}</>}.
            Generate a new token in the Zoho API console and paste it here; the old one
            stops working when you revoke it there.
          </p>
          <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
            <input
              className="input"
              type="password"
              placeholder="New refresh token"
              style={{ maxWidth: 340 }}
              value={rotateToken}
              aria-label="New refresh token"
              onChange={(e) => setRotateToken(e.target.value)}
            />
            <button
              className="btn btn-secondary btn-sm"
              disabled={!rotateToken.trim()}
              onClick={() => doRotate(cr.credential_id)}
            >
              Rotate
            </button>
          </div>
          {credMsg && <p className="cx-detail">{credMsg}</p>}
        </Bp>
      ))}

      {!status && !error ? (
        <div className="skeleton" style={{ height: 90 }} />
      ) : (
        <>
          <div className="dp-split-2">
            <Bp style={{ padding: "8px 14px" }}>
              <div className="section-h" style={{ marginTop: 8 }}>Last sync</div>
              {!s ? (
                <p className="conn-detail" style={{ padding: "0 0 12px" }}>
                  No sync has run yet. Press <b>Sync every company</b> to pull.
                </p>
              ) : (
                <table className="facttable">
                  <tbody>
                    <tr><td>Result</td><td className="fv">{RESULT_UI[s.status] || s.status}</td></tr>
                    <tr><td>When</td><td className="fv">{when(s.started_at)}</td></tr>
                    <tr>
                      <td>
                        Read from
                        <div className="fsrc">the start date this run was given</div>
                      </td>
                      <td className="fv">{s.since || "rolling window"}</td>
                    </tr>
                    <tr><td>Customers</td><td className="fv">{s.customers}</td></tr>
                    <tr><td>Products</td><td className="fv">{s.products}</td></tr>
                    <tr><td>Sales lines</td><td className="fv">{s.sales_txns}</td></tr>
                    <tr>
                      <td>
                        <Labelled tip="Bill lines — what the stock cost. Without these there is no margin anywhere in the platform, only revenue. A zero here almost always means the ZohoBooks.bills.READ scope was not granted.">
                          Cost records
                        </Labelled>
                      </td>
                      <td className="fv">{s.cost_records}</td>
                    </tr>
                    <tr>
                      <td>
                        Documents read
                        <div className="fsrc">
                          {s.documents_resumed
                            ? `${s.documents_resumed} were already held and were not read again`
                            : "invoices and bills fetched individually"}
                        </div>
                      </td>
                      <td className="fv">{s.documents_fetched}</td>
                    </tr>
                    <tr>
                      <td>
                        <Labelled tip="Maps a Zoho salesperson to a platform account. Needs the ZohoBooks.users.READ scope — without it accounts stay unassigned and every decision routes to management.">
                          Accounts assigned
                        </Labelled>
                        <div className="fsrc">from Zoho's salesperson on the latest invoice</div>
                      </td>
                      <td className="fv">{s.assignments}</td>
                    </tr>
                    <tr><td>Signals detected</td><td className="fv">{s.signals_emitted}</td></tr>
                    <tr><td>Decisions created</td><td className="fv">{s.decisions_created}</td></tr>
                    <tr>
                      <td>
                        <Labelled tip="A row Zoho returned that could not be used — a line with no item, a document in a currency this organization does not trade in. Recorded with a reason so the gap is explainable, never dropped silently.">
                          Rows skipped
                        </Labelled>
                      </td>
                      <td className="fv">{s.skipped_count}</td>
                    </tr>
                  </tbody>
                </table>
              )}
              {s?.error && (
                <div className="state-panel" style={{ margin: "0 0 12px" }}>
                  <div className="state-mark">
                    {s.status === "PARTIAL" ? "Why it stopped" : "Why it failed"}
                  </div>
                  <p style={{ margin: 0, fontSize: 13 }}>{s.error}</p>
                  {s.status === "PARTIAL" && (
                    <p style={{ margin: "8px 0 0", fontSize: 13 }}>
                      The rows above were kept. Running the sync again continues from
                      here rather than starting over.
                    </p>
                  )}
                </div>
              )}
            </Bp>

            <Bp style={{ padding: "8px 14px" }}>
              <div className="section-h" style={{ marginTop: 8 }}>
                <Labelled tip="Everything currently held for this organization, pooled across every connected company. These are the rows the analysis actually runs on.">
                  What is in the read model now
                </Labelled>
              </div>
              <table className="facttable">
                <tbody>
                  {Object.entries(status?.read_model || {}).map(([k, v]) => (
                    <tr key={k}>
                      <td>{k.replace(/_/g, " ").replace(/^\w/, (m) => m.toUpperCase())}</td>
                      <td className="fv">{v}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Bp>
          </div>

          {s && s.skipped_count > 0 && (
            <>
              <div className="section-h">Skipped rows</div>
              <Bp style={{ padding: 2 }}>
                <table className="dp-table">
                  <thead>
                    <tr><th>Kind</th><th>Reference</th><th>Reason</th></tr>
                  </thead>
                  <tbody>
                    {s.skipped_sample.map((r, i) => (
                      <tr key={i}>
                        <td>{r.kind}</td>
                        <td>{r.ref}</td>
                        <td>{r.code} — {r.detail}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Bp>
            </>
          )}
        </>
      )}
    </div>
  );
}
