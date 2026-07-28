import { useCallback, useEffect, useState } from "react";
import { papi } from "./api";
import type { DataStatus, PlatformSession, ZohoConnectionInput } from "./types";
import { Bp } from "./ui";

/**
 * Data & connection.
 *
 * Answers one question without requiring a terminal: is this showing the
 * company's real books, or sample data? The state is stated in words rather
 * than left to be inferred from whether numbers look plausible — a demo
 * dataset and a live one are indistinguishable by eye, which is exactly how
 * someone ends up trusting a decision built on fixtures.
 */

const STATE_UI: Record<string, { label: string; tone: "ok" | "warn" | "bad" }> = {
  CONNECTED: { label: "Connected", tone: "ok" },
  SAMPLE_DATA: { label: "Sample data", tone: "warn" },
  NOT_CONFIGURED: { label: "Not connected", tone: "warn" },
  WRONG_ORG: { label: "Wrong organization", tone: "bad" },
  ERROR: { label: "Rejected", tone: "bad" },
  UNREACHABLE: { label: "Unreachable", tone: "bad" },
};

/** Data-centre presets — a refresh token issued in one is rejected by every
 *  other, so picking the right row up front avoids the single most common
 *  setup failure (see docs/zoho-setup.md). */
const DC_PRESETS: { label: string; accounts_base: string; api_base: string }[] = [
  { label: "India (.in)", accounts_base: "https://accounts.zoho.in",
    api_base: "https://www.zohoapis.in/books/v3" },
  { label: "United States (.com)", accounts_base: "https://accounts.zoho.com",
    api_base: "https://www.zohoapis.com/books/v3" },
  { label: "Europe (.eu)", accounts_base: "https://accounts.zoho.eu",
    api_base: "https://www.zohoapis.eu/books/v3" },
  { label: "Australia (.com.au)", accounts_base: "https://accounts.zoho.com.au",
    api_base: "https://www.zohoapis.com.au/books/v3" },
  { label: "Japan (.jp)", accounts_base: "https://accounts.zoho.jp",
    api_base: "https://www.zohoapis.jp/books/v3" },
];

const EMPTY_CONN_FORM: ZohoConnectionInput = {
  zoho_organization_id: "", client_id: "", client_secret: "", refresh_token: "",
  accounts_base: DC_PRESETS[0].accounts_base, api_base: DC_PRESETS[0].api_base,
};

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
  const [checking, setChecking] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [since, setSince] = useState<string>(defaultSince());
  const [full, setFull] = useState(false);
  const [connForm, setConnForm] = useState<ZohoConnectionInput>(EMPTY_CONN_FORM);
  const [connecting, setConnecting] = useState(false);
  const [connError, setConnError] = useState<string | null>(null);
  const [disconnecting, setDisconnecting] = useState(false);

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

  async function connect(e: React.FormEvent) {
    e.preventDefault();
    setConnecting(true);
    setConnError(null);
    try {
      await papi.setZohoConnection(session.token, {
        ...connForm,
        zoho_organization_id: connForm.zoho_organization_id.trim(),
        client_id: connForm.client_id.trim(),
        client_secret: connForm.client_secret.trim(),
        refresh_token: connForm.refresh_token.trim(),
      });
      setConnForm(EMPTY_CONN_FORM);
      await load();
      onSynced();
    } catch (e) {
      setConnError((e as Error).message);
    } finally {
      setConnecting(false);
    }
  }

  async function disconnect() {
    if (!window.confirm(
      "Disconnect this Zoho account? Data already pulled stays put — only the " +
      "credentials are removed, and syncing stops until reconnected.")) {
      return;
    }
    setDisconnecting(true);
    try {
      await papi.clearZohoConnection(session.token);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setDisconnecting(false);
    }
  }

  async function sync() {
    setSyncing(true);
    setResult(null);
    setError(null);
    try {
      const r = await papi.runSync(session.token, { since: since || undefined, full });
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
    }
  }

  const c = status?.connection;
  const ui = c ? STATE_UI[c.state] || STATE_UI.ERROR : null;
  const s = status?.last_sync;

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

      {!status && !error ? (
        <div className="skeleton" style={{ height: 90 }} />
      ) : c && ui ? (
        <>
          <Bp className={`conn conn-${ui.tone}`} style={{ padding: 18, marginBottom: 14 }}>
            <div className="conn-top">
              <span className={`conn-badge ${ui.tone}`}>{ui.label}</span>
              <span className="conn-src">
                {c.source === "api" ? "Zoho Books · live" : "Offline sample source"}
              </span>
            </div>
            <div className="conn-headline">{c.headline}</div>
            {c.detail && <p className="conn-detail">{c.detail}</p>}

            {c.state === "CONNECTED" && (
              <dl className="conn-facts">
                <div><dt>Organization</dt><dd>{c.organization_name}</dd></div>
                <div><dt>Organization id</dt><dd>{c.organization_id}</dd></div>
                <div><dt>Currency</dt><dd>{c.currency || "—"}</dd></div>
                <div><dt>Default history</dt><dd>{c.history_days} days</dd></div>
              </dl>
            )}
            {c.state === "WRONG_ORG" && (c.visible_organizations?.length ?? 0) > 0 && (
              <dl className="conn-facts">
                {c.visible_organizations!.map((o) => (
                  <div key={o.organization_id}>
                    <dt>{o.name}</dt>
                    <dd>{o.organization_id}</dd>
                  </div>
                ))}
              </dl>
            )}

            {c.state === "NOT_CONFIGURED" && status?.can_manage_connection && (
              <form className="sync-opts" onSubmit={connect}>
                <label htmlFor="conn-zoho-org">
                  Zoho Books organization id
                  <span className="fsrc">
                    Settings → Organization Profile in Zoho Books, or the id in its URL.
                  </span>
                </label>
                <input id="conn-zoho-org" className="input" required
                  value={connForm.zoho_organization_id}
                  onChange={(e) => setConnForm({ ...connForm, zoho_organization_id: e.target.value })} />

                <label htmlFor="conn-dc" style={{ marginTop: 10 }}>
                  Data centre
                  <span className="fsrc">
                    A refresh token issued in one is rejected by every other — this is the
                    single most common setup failure. Match it to the account.
                  </span>
                </label>
                <select id="conn-dc" className="input"
                  value={connForm.accounts_base}
                  onChange={(e) => {
                    const p = DC_PRESETS.find((d) => d.accounts_base === e.target.value);
                    if (p) setConnForm({ ...connForm, accounts_base: p.accounts_base, api_base: p.api_base });
                  }}>
                  {DC_PRESETS.map((p) => (
                    <option key={p.accounts_base} value={p.accounts_base}>{p.label}</option>
                  ))}
                </select>

                <label htmlFor="conn-client-id" style={{ marginTop: 10 }}>Client ID</label>
                <input id="conn-client-id" className="input" required
                  value={connForm.client_id}
                  onChange={(e) => setConnForm({ ...connForm, client_id: e.target.value })} />

                <label htmlFor="conn-client-secret" style={{ marginTop: 10 }}>Client secret</label>
                <input id="conn-client-secret" type="password" className="input" required
                  value={connForm.client_secret}
                  onChange={(e) => setConnForm({ ...connForm, client_secret: e.target.value })} />

                <label htmlFor="conn-refresh-token" style={{ marginTop: 10 }}>
                  Refresh token
                  <span className="fsrc">Encrypted before it is stored; never shown again.</span>
                </label>
                <input id="conn-refresh-token" type="password" className="input" required
                  value={connForm.refresh_token}
                  onChange={(e) => setConnForm({ ...connForm, refresh_token: e.target.value })} />

                {connError && (
                  <p className="conn-detail" style={{ color: "var(--color-danger, #b3261e)" }}>
                    {connError}
                  </p>
                )}
                <div style={{ marginTop: 12 }}>
                  <button type="submit" className="btn btn-primary btn-sm" disabled={connecting}>
                    {connecting ? "Connecting…" : "Connect Zoho"}
                  </button>
                </div>
              </form>
            )}
            {c.state === "NOT_CONFIGURED" && !status?.can_manage_connection && (
              <p className="conn-detail" style={{ marginTop: 10 }}>
                Ask an owner to connect this organization's Zoho account.
              </p>
            )}

            {status?.can_sync && c.state !== "NOT_CONFIGURED" && (
              <div className="sync-opts">
                <label htmlFor="sync-since">
                  Read the books from
                  <span className="fsrc">
                    Every invoice and bill after this date is read individually, so an
                    earlier date means a longer pull. The detectors compare the last 90
                    days against the 90 before that, and need six months of history
                    before they will call a decline — a year and a half covers all of it.
                  </span>
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
                </label>
              </div>
            )}

            <div style={{ display: "flex", gap: 8, marginTop: 14, flexWrap: "wrap" }}>
              <button className="btn btn-secondary btn-sm" onClick={load} disabled={checking}>
                {checking ? "Checking…" : "Re-check connection"}
              </button>
              {status?.can_sync && c.state !== "NOT_CONFIGURED" && (
                <button className="btn btn-primary btn-sm" onClick={sync} disabled={syncing}>
                  {syncing ? "Syncing…" : "Sync now"}
                </button>
              )}
              {status?.can_manage_connection && c.state !== "NOT_CONFIGURED" && c.state !== "SAMPLE_DATA" && (
                <button className="btn btn-secondary btn-sm" onClick={disconnect} disabled={disconnecting}>
                  {disconnecting ? "Disconnecting…" : "Disconnect"}
                </button>
              )}
            </div>
            {status && !status.can_sync && c.state !== "NOT_CONFIGURED" && (
              <p className="conn-detail" style={{ marginTop: 10 }}>
                Syncing is a manager or owner action.
              </p>
            )}
            {result && <div className="conn-result">{result}</div>}
          </Bp>

          <div className="dp-split-2">
            <Bp style={{ padding: "8px 14px" }}>
              <div className="section-h" style={{ marginTop: 8 }}>Last sync</div>
              {!s ? (
                <p className="conn-detail" style={{ padding: "0 0 12px" }}>
                  No sync has run yet. Press <b>Sync now</b> to pull from{" "}
                  {c.source === "api" ? "Zoho" : "the sample source"}.
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
                    <tr><td>Cost records</td><td className="fv">{s.cost_records}</td></tr>
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
                        Accounts assigned
                        <div className="fsrc">from Zoho's salesperson on the latest invoice</div>
                      </td>
                      <td className="fv">{s.assignments}</td>
                    </tr>
                    <tr><td>Signals detected</td><td className="fv">{s.signals_emitted}</td></tr>
                    <tr><td>Decisions created</td><td className="fv">{s.decisions_created}</td></tr>
                    <tr>
                      <td>
                        Rows skipped
                        <div className="fsrc">recorded with a reason, never dropped silently</div>
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
              <div className="section-h" style={{ marginTop: 8 }}>What is in the read model now</div>
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
      ) : null}
    </div>
  );
}
