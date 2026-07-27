import { useCallback, useEffect, useState } from "react";
import { papi } from "./api";
import type { DataStatus, PlatformSession } from "./types";
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
  WRONG_ORG: { label: "Wrong organization", tone: "bad" },
  ERROR: { label: "Rejected", tone: "bad" },
  UNREACHABLE: { label: "Unreachable", tone: "bad" },
};

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

  async function sync() {
    setSyncing(true);
    setResult(null);
    setError(null);
    try {
      const r = await papi.runSync(session.token);
      setResult(
        r.run.status === "OK"
          ? `Pulled ${r.run.sales_txns} sales lines and ${r.run.cost_records} cost records — ` +
            `${r.run.signals_emitted} signals, ${r.run.decisions_created} new decisions.`
          : `Sync failed: ${r.run.error}`,
      );
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
                <div><dt>History pulled</dt><dd>{c.history_days} days</dd></div>
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

            <div style={{ display: "flex", gap: 8, marginTop: 14, flexWrap: "wrap" }}>
              <button className="btn btn-secondary btn-sm" onClick={load} disabled={checking}>
                {checking ? "Checking…" : "Re-check connection"}
              </button>
              {status?.can_sync && (
                <button className="btn btn-primary btn-sm" onClick={sync} disabled={syncing}>
                  {syncing ? "Syncing…" : "Sync now"}
                </button>
              )}
            </div>
            {status && !status.can_sync && (
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
                    <tr><td>Result</td><td className="fv">{s.status === "OK" ? "Succeeded" : "Failed"}</td></tr>
                    <tr><td>When</td><td className="fv">{when(s.started_at)}</td></tr>
                    <tr><td>Customers</td><td className="fv">{s.customers}</td></tr>
                    <tr><td>Products</td><td className="fv">{s.products}</td></tr>
                    <tr><td>Sales lines</td><td className="fv">{s.sales_txns}</td></tr>
                    <tr><td>Cost records</td><td className="fv">{s.cost_records}</td></tr>
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
                  <div className="state-mark">Why it failed</div>
                  <p style={{ margin: 0, fontSize: 13 }}>{s.error}</p>
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
