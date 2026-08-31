import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Button from "@mui/material/Button";
import { useCallback, useEffect, useState } from "react";
import Box from "@mui/material/Box";
import { DataGrid, numeric } from "./DataGrid";
import { money } from "../money";
import { formatDateTime, since as when, todayISO } from "../when";
import { papi } from "./api";
import { ErrorState, LoadingState } from "./kit";
import { ConnectionsPanel } from "./ConnectionsPanel";
import { RunLogPanel } from "./RunLogPanel";
import { SkippedRowsPanel } from "./SkippedRowsPanel";
import { SyncStatusCard, useSync } from "./SyncStatus";
import type { DataStatus, PlatformSession, UnresolvedReference } from "./types";
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
  const d = new Date(`${todayISO()}T00:00:00`);
  d.setMonth(d.getMonth() - 18);
  d.setDate(1);
  return d.toLocaleDateString("en-CA");
}

export function DataScreen({ session, onSynced }: { session: PlatformSession; onSynced: () => void }) {
  const [status, setStatus] = useState<DataStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [since, setSince] = useState<string>(defaultSince());
  // Whether the operator has chosen a date themselves. Until they do, the
  // field follows the organization's existing coverage once the status
  // arrives — "sync again" should mean "the window I already have", not
  // "eighteen months because that is the constant in the code".
  const [sinceTouched, setSinceTouched] = useState(false);
  const [full, setFull] = useState(false);

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

  const coveredFrom = status?.auto_sync?.covers_from ?? null;
  useEffect(() => {
    if (coveredFrom && !sinceTouched) setSince(coveredFrom);
  }, [coveredFrom, sinceTouched]);

  // Starting a sync and watching it are one concern, in one place — see
  // SyncStatus.tsx. The screen renders from that state rather than from the
  // response to whatever request it last made, which is what lets a page
  // opened mid-pull show the pull.
  const sync = useSync(session.token, useCallback(() => {
    // A job just finished: the read-model counts on this page are now stale.
    load();
    onSynced();
  }, [load, onSynced]));

  /** Queue a pull. `connectionId` names one company; omitted means every
   *  enabled one. Returns immediately — the card takes it from there. */
  async function startSync(connectionId?: string, fromDate?: string, reread?: boolean) {
    await sync.start({
      since: (fromDate ?? since) || undefined,
      full: reread ?? full,
      connection_id: connectionId,
    });
    setFull(false);
  }

  const s = status?.last_sync;
  const canSync = Boolean(status?.can_sync);
  // Which companies are pulling *right now*, by connection. This used to be one
  // organization-wide flag, and that flag disabled all three companies' buttons
  // the moment any one of them started — so the per-connection concurrency the
  // server grew was unreachable from the screen that would have used it.
  const busyConnections = (sync.state?.busy_connections ?? []) as string[];
  // The "every company" button is a different question and keeps the old
  // answer: it is one pull across all of them, and two of those really would
  // read the same books twice.
  const allBusy = !(sync.state?.can_start ?? true) || sync.busy;

  return (
    <div>
      <div className="dp-head">
        <h1>Data &amp; connection</h1>
        <p>Where the numbers come from, and when they last arrived.</p>
      </div>

      {error && (
        <Box sx={{ mb: 2 }}>
          {/* `onRetry`, because without it this error came with no way to act
              on it. The "Refresh status" button that would otherwise re-fetch
              sits behind `canSync`, which is read off `status` — the very call
              that just failed — so on a first-load failure it is hidden from
              everyone, managers and owners included, not only from the
              salesperson who never had it. */}
          <ErrorState title="Could not read the connection status" error={error}
                      onRetry={load} busy={checking} />
        </Box>
      )}

      <ConnectionsPanel
        session={session}
        canSync={canSync}
        onSync={(id, from, reread) => startSync(id, from, reread)}
        // An all-companies run reads *this* company too, so its own button is
        // busy for the duration. It reports itself with a NULL connection, so
        // it never appears in `busy_connections` — without this, every card
        // offered a pull the server would decline by handing back the umbrella
        // job, which looks like a button that does nothing.
        busyConnections={busyConnections}
        everyCompanyBusy={Boolean(sync.state?.active
                                  && sync.state.active.connection_id == null)}
        starting={sync.busy}
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
                max={todayISO()}
                onChange={(e) => { setSinceTouched(true); setSince(e.target.value); }}
              />
              <label className="sync-check">
                <input type="checkbox" checked={full} onChange={(e) => setFull(e.target.checked)} />
                Re-read everything, including documents already held
                <Tip text="Normally a pull skips documents it already holds, which is what makes a repeat run fast. Tick this after granting a scope that was missing — the rows are there, but the fields that scope unlocks are not." />
              </label>
            </div>

            <div style={{ display: "flex", gap: 8, marginTop: 14, flexWrap: "wrap", alignItems: "center" }}>
              <Button variant="outlined" size="small" onClick={load} disabled={checking}>
                {checking ? "Checking…" : "Refresh status"}
              </Button>
              <Button
                variant="contained" size="small"
                onClick={() => startSync()}
                disabled={allBusy}
              >
                {sync.state?.active ? "Sync running…" : sync.busy ? "Starting…" : "Sync every company"}
              </Button>
              <Tip text="Runs one pull per enabled company, in turn. To pull just one, use the button on its card above." />
              {sync.state?.active && (
                <span className="st-help">
                  A sync is already running — starting another would pull the same books twice.
                </span>
              )}
            </div>

            {/* ── the automatic pull ── */}
            {status?.auto_sync && (
              <div className="sync-opts" style={{ marginTop: 14 }}>
                <label htmlFor="auto-sync">
                  <Labelled tip="Each automatic pull re-reads the organization's existing window — documents already held and unchanged cost nothing, and a bill entered today but dated last week is still caught, which a pull starting 'from the last sync' would silently miss.">
                    Automatic sync
                  </Labelled>
                </label>
                {!status.auto_sync.available ? (
                  <span className="st-help">
                    Applies to a live Zoho connection — this deployment is showing sample data.
                  </span>
                ) : (
                  <>
                    <select
                      id="auto-sync"
                      className="input"
                      value={String(status.auto_sync.hours)}
                      onChange={async (e) => {
                        const hours = Number(e.target.value);
                        try {
                          const r = await papi.setAutoSync(session.token, hours);
                          setStatus((s) => (s ? { ...s, auto_sync: r.auto_sync } : s));
                        } catch (err) {
                          setError((err as Error).message);
                        }
                      }}
                    >
                      <option value="0">Off</option>
                      {[1, 3, 6, 12, 24].map((h) => (
                        <option key={h} value={String(h)}>
                          Every {h === 1 ? "hour" : `${h} hours`}
                        </option>
                      ))}
                    </select>
                    <span className="st-help">
                      {status.auto_sync.hours === 0
                        ? "Off — the books refresh only when somebody syncs."
                        : `Next around ${formatDateTime(status.auto_sync.next_run_at)}` +
                          (status.auto_sync.covers_from
                            ? `, re-reading from ${status.auto_sync.covers_from}.`
                            : ".")}
                    </span>
                  </>
                )}
              </div>
            )}
          </>
        )}
      </Bp>

      {/* ── the job itself: state first, never the last response ── */}
      <div className="section-h">Sync status</div>
      <SyncStatusCard sync={sync} canSync={canSync} onRetry={() => startSync()} />

      {/* Rotation used to be a panel of its own here. It has moved onto the
          connection card above — a revocable refresh token is a Zoho
          mechanism, not a concept every connector will have, and the control
          belongs on the thing somebody has just decided to rotate rather than
          three sections further down the page. */}

      {!status && !error ? (
        <LoadingState rows={1} height={90} label="Reading the sync status…" />
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
                    {/* The supply stage. Shown unconditionally, including at
                        zero: "suppliers 0" with a reason underneath is the
                        answer to "why are vendors not being read", and an
                        absent row is not. */}
                    <tr>
                      <td>
                        <Labelled tip="Suppliers, from the same Zoho contact list as customers. Needs no scope beyond ZohoBooks.contacts.READ, so a zero here means the stage did not run rather than that it was refused.">
                          Suppliers
                        </Labelled>
                      </td>
                      <td className="fv">{s.vendors}</td>
                    </tr>
                    <tr>
                      <td>
                        <Labelled tip="Money in, with the invoices each receipt settled. Needs ZohoBooks.customerpayments.READ. Without it every invoice looks paid the day it was raised, and the Cash screen is empty.">
                          Payments
                        </Labelled>
                      </td>
                      <td className="fv">{s.payments}</td>
                    </tr>
                    <tr>
                      <td>
                        <Labelled tip="What is on the way from suppliers. Needs ZohoBooks.purchaseorders.READ.">
                          Purchase orders
                        </Labelled>
                      </td>
                      <td className="fv">{s.purchase_orders}</td>
                    </tr>
                    <tr>
                      <td>
                        <Labelled tip="Orders customers have placed but that have not been invoiced yet — demand already promised. Needs ZohoBooks.salesorders.READ.">
                          Sales orders
                        </Labelled>
                      </td>
                      <td className="fv">{s.sales_orders}</td>
                    </tr>
                    <tr>
                      <td>
                        <Labelled tip="Money out, to suppliers. Needs ZohoBooks.vendorpayments.READ. Payments in alone are revenue collected, not cash — both sides are needed before liquidity means anything.">
                          Payments out
                        </Labelled>
                      </td>
                      <td className="fv">{s.vendor_payments}</td>
                    </tr>
                    <tr>
                      <td>
                        Stock snapshots
                        <div className="fsrc">read from the item list, at no extra cost</div>
                      </td>
                      <td className="fv">{s.stock_snapshots}</td>
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
                /* Warning, not error, when the run was PARTIAL: rows were kept
                   and the next run continues from here, so the severity that
                   says "nothing survived" would overstate it. */
                <Alert
                  severity={s.status === "PARTIAL" ? "warning" : "error"}
                  sx={{ mb: 1.5 }}
                >
                  <AlertTitle>
                    {s.status === "PARTIAL" ? "Why it stopped" : "Why it failed"}
                  </AlertTitle>
                  {s.error}
                  {s.status === "PARTIAL" && (
                    <Box sx={{ mt: 1 }}>
                      The rows above were kept. Running the sync again continues from
                      here rather than starting over.
                    </Box>
                  )}
                </Alert>
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

          {s && (s.unresolved?.length ?? 0) > 0 && (
            <>
              <div className="section-h">
                <Labelled tip="Grouped by what is missing rather than by row: one discontinued item on four hundred bill lines is one thing to fix, and listing it four hundred times hides the other two.">
                  What could not be resolved
                </Labelled>
              </div>
              <DataGrid<UnresolvedReference>
                ariaLabel="Unresolved references"
                pageSize={10}
                rows={s.unresolved}
                columns={[
                  {
                    headerName: "What is missing", flex: 1.3, minWidth: 240,
                    filter: "agTextColumnFilter",
                    valueGetter: (p) =>
                      p.data?.label || p.data?.missing_id || p.data?.code || "",
                    cellRenderer: (p: { data?: UnresolvedReference }) => p.data ? (
                      <div>
                        <b>{p.data.label || p.data.missing_id || p.data.code}</b>
                        <div className="fsrc">
                          {p.data.sku ? `SKU ${p.data.sku} · ` : ""}
                          {p.data.kind.replace(/_/g, " ")} · {p.data.code}
                          {p.data.missing_id ? ` · id ${p.data.missing_id}` : ""}
                        </div>
                      </div>
                    ) : null,
                  },
                  numeric<UnresolvedReference>("lines", "Lines held up",
                                               (n) => String(n), {
                    width: 145, flex: 0, sort: "desc",
                    headerTooltip: "How many document lines this one missing "
                      + "record is blocking. The list is ranked by it.",
                  }),
                  numeric<UnresolvedReference>("value", "Value", money,
                                               { width: 140, flex: 0 }),
                  {
                    headerName: "Seen on", flex: 1, minWidth: 190,
                    sortable: false, filter: false, autoHeight: true,
                    cellRenderer: (p: { data?: UnresolvedReference }) => p.data ? (
                      <div style={{ padding: "4px 0" }}>
                        {p.data.examples.length === 0 ? (
                          <span className="fsrc">{p.data.first_seen || "—"}</span>
                        ) : p.data.examples.map((e, j) => (
                          <div key={j} className="fsrc">
                            {e.document}{e.date ? ` · ${e.date}` : ""}
                            {e.party ? ` · ${e.party}` : ""}
                          </div>
                        ))}
                        {p.data.lines > p.data.examples.length
                          && p.data.examples.length > 0 && (
                          <div className="fsrc">
                            and {p.data.lines - p.data.examples.length} more
                          </div>
                        )}
                      </div>
                    ) : null,
                  },
                  {
                    field: "fix", headerName: "What to do", flex: 1.6,
                    minWidth: 280, sortable: false, filter: false,
                    autoHeight: true, wrapText: true,
                  },
                ]}
              />
            </>
          )}

          {/* The rows themselves are kept below the worklist, not instead of
              it: the worklist is what to do, these are the evidence it was
              built from — and the sheet somebody reconciles against Zoho. */}
          {s && s.skipped_count > 0 && (
            <SkippedRowsPanel token={session.token} run={s} canExport={canSync} />
          )}

          {/* And what the pull recorded as it ran. Last because it is the
              deepest thing here — the counters above answer "what landed", the
              worklist answers "what to fix", and this answers "what happened",
              which is the only one of the three that can explain a run that
              stopped an hour in. Managers and owners only, like the rows above:
              a log line is whatever the code passed to it. */}
          {s && canSync && (
            <RunLogPanel token={session.token} runId={s.sync_run_id}
                         running={s.active} />
          )}
        </>
      )}
    </div>
  );
}
