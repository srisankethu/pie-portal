import { useCallback, useEffect, useRef, useState } from "react";
import { papi } from "./api";
import type { SyncOptions, SyncRun, SyncState } from "./types";
import { Bp, Labelled, Tip } from "./ui";

/**
 * The sync status card, and the hook behind it.
 *
 * A pull reads every invoice and bill individually, so a real organization's
 * first sync takes minutes. The old flow held the request open for all of it
 * and answered a second click with "Sync is in progress" — a sentence that
 * does not say which sync, when it started, what it is doing, or whether it is
 * still alive. The user could not tell a working sync from a hung one.
 *
 * So the screen renders from persisted state, not from the outcome of whatever
 * request it last made. Open the page mid-pull and you see the pull. Reload,
 * navigate away, come back tomorrow — the job lives in the database, not in
 * this tab.
 *
 * **The progress bar measures calendar coverage, not documents.** Zoho will not
 * say how many invoices it holds until they have been paged through, so a
 * document percentage would be invented. The pull is therefore read in monthly
 * slices, and the months between the start date and today are known before the
 * first call — so "month 7 of 18" is a fact. It is labelled as what it is: how
 * much of the requested window has been read, not an estimate of time
 * remaining. Months differ wildly in volume and the card says so, because a bar
 * that implies an ETA it cannot support is the same lie in a nicer shape.
 */

/** How often to ask while a job is in flight. Fast enough to feel live,
 *  slow enough that a long pull is not answering a request every second. */
const POLL_MS = 2500;

export interface Sync {
  state: SyncState | null;
  error: string | null;
  /** Set when the last start returned an existing job instead of a new one. */
  note: string | null;
  busy: boolean;
  start: (opts?: SyncOptions) => Promise<void>;
  refresh: () => Promise<void>;
}

/**
 * One place that knows how to start a sync and watch it.
 *
 * Shared rather than reimplemented per screen: the connections list and the
 * pull panel both start syncs, and two polling loops with two ideas of "is it
 * running" would eventually disagree in front of a user.
 */
export function useSync(token: string, onFinished?: () => void): Sync {
  const [state, setState] = useState<SyncState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Used to notice the moment a job stops being active, which is when the rest
  // of the screen needs re-reading — the read-model counts have just changed.
  const wasActive = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const next = await papi.syncState(token);
      setState(next);
      setError(null);
      if (wasActive.current && !next.active) {
        onFinished?.();
      }
      wasActive.current = Boolean(next.active);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [token, onFinished]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Poll only while something is happening. An idle organization should not be
  // generating a request every few seconds for no reason.
  useEffect(() => {
    if (!state?.active) return;
    const id = window.setInterval(refresh, POLL_MS);
    return () => window.clearInterval(id);
  }, [state?.active, refresh]);

  const start = useCallback(
    async (opts: SyncOptions = {}) => {
      setBusy(true);
      setError(null);
      setNote(null);
      try {
        const r = await papi.runSync(token, opts);
        setState(r);
        wasActive.current = Boolean(r.active);
        // Said out loud when the click did not do what it looked like it did.
        if (!r.started) setNote(r.note);
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [token],
  );

  return { state, error, note, busy, start, refresh };
}

/* ── formatting ─────────────────────────────────────────────────────────── */

function elapsed(fromIso: string | null, toIso?: string | null): string {
  if (!fromIso) return "—";
  const from = new Date(fromIso).getTime();
  const to = toIso ? new Date(toIso).getTime() : Date.now();
  const s = Math.max(0, Math.round((to - from) / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

function at(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-IN", {
    day: "numeric", month: "short", hour: "numeric", minute: "2-digit",
  });
}

function ago(iso: string | null): string {
  if (!iso) return "never";
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)} h ago`;
  return at(iso);
}

const LABEL: Record<string, string> = {
  IDLE: "Never synced",
  QUEUED: "Queued",
  RUNNING: "Running",
  OK: "Completed",
  PARTIAL: "Stopped part-way",
  FAILED: "Failed",
};

const TONE: Record<string, string> = {
  IDLE: "idle", QUEUED: "busy", RUNNING: "busy",
  OK: "ok", PARTIAL: "warn", FAILED: "bad",
};

/** A ticking clock, so an elapsed time that is meant to be live actually is. */
function useTick(on: boolean): void {
  const [, setN] = useState(0);
  useEffect(() => {
    if (!on) return;
    const id = window.setInterval(() => setN((n) => n + 1), 1000);
    return () => window.clearInterval(id);
  }, [on]);
}

function Summary({ run }: { run: SyncRun }) {
  const removed = run.notes?.demo_data_removed;
  return (
    <>
      <dl className="sy-summary">
        <div><dt>Customers</dt><dd>{run.customers}</dd></div>
        <div><dt>Items</dt><dd>{run.products}</dd></div>
        <div><dt>Sales lines</dt><dd>{run.sales_txns}</dd></div>
        <div>
          <dt>
            <Labelled tip="Bill lines — what the stock cost. Without these there is no margin anywhere in the platform, only revenue. A zero here usually means the ZohoBooks.bills.READ scope was not granted.">
              Cost records
            </Labelled>
          </dt>
          <dd>{run.cost_records}</dd>
        </div>
        <div><dt>Signals</dt><dd>{run.signals_emitted}</dd></div>
        <div><dt>Decisions</dt><dd>{run.decisions_created}</dd></div>
      </dl>
      {run.skipped_count > 0 && (
        <p className="sy-detail">
          {run.skipped_count} row{run.skipped_count === 1 ? "" : "s"} skipped, each with a
          recorded reason — see below.
        </p>
      )}
      {removed && (
        <p className="sy-detail">
          Cleared the leftover sample data ({removed.customers ?? 0} customers,{" "}
          {removed.decisions ?? 0} decisions) now that real books have arrived.
        </p>
      )}
    </>
  );
}

/* ── the card ───────────────────────────────────────────────────────────── */

export function SyncStatusCard({
  sync,
  canSync,
  onRetry,
}: {
  sync: Sync;
  canSync: boolean;
  /** Re-runs whatever the screen considers the current request. */
  onRetry?: () => void;
}) {
  const { state, error, note } = sync;
  const active = state?.active ?? null;
  useTick(Boolean(active));

  if (!state) return <div className="skeleton" style={{ height: 88 }} />;

  const status = state.state;
  const tone = TONE[status] ?? "idle";
  const shown = active ?? state.last;

  return (
    <Bp className={`sy sy-${tone}`}>
      <div className="sy-head">
        <span className={`sy-badge ${tone}`}>
          {active && <span className="sy-spin" aria-hidden="true" />}
          {LABEL[status] ?? status}
        </span>
        <span className="sy-last">
          Last successful sync&nbsp;<b>{ago(state.last_successful_at)}</b>
          <Tip text="The last run that finished cleanly. A run that stopped part-way is not counted here — it wrote rows, but calling it successful would overstate what is actually held." />
        </span>
      </div>

      {active ? (
        <>
          <div className="sy-line">
            <span className="sy-phase">{active.phase || "Working"}</span>
            <span className="sy-elapsed">{elapsed(active.started_at)} elapsed</span>
          </div>
          {active.windows_total > 1 && (
            <div className="sy-progress">
              <div className="sy-bar" role="progressbar"
                   aria-valuenow={active.windows_done}
                   aria-valuemin={0} aria-valuemax={active.windows_total}
                   aria-label="Months of the requested window read">
                <i style={{ width: `${(active.windows_done / active.windows_total) * 100}%` }} />
              </div>
              <span className="sy-bar-label">
                {active.windows_done} of {active.windows_total} months read
              </span>
            </div>
          )}
          <p className="sy-detail">
            Started {at(active.started_at)}. This runs in the background — you can
            leave this page, and it will still be going when you come back.
          </p>
          <p className="sy-detail sy-quiet">
            {active.windows_total > 1
              ? "That bar is how much of the requested date range has been read, not " +
                "an estimate of time left — a busy month takes far longer than a quiet " +
                "one. Zoho does not reveal how many documents it holds until they have " +
                "been paged through, so no honest document percentage exists."
              : "No percentage is shown for a range this short: Zoho does not say how " +
                "many documents it will return until they have been paged through. The " +
                "stage above and the elapsed time are real."}
          </p>
        </>
      ) : shown ? (
        <>
          <div className="sy-line">
            <span className="sy-phase">
              {status === "OK" && "Finished cleanly"}
              {status === "PARTIAL" && "Stopped before it finished"}
              {status === "FAILED" && "Did not complete"}
            </span>
            <span className="sy-elapsed">
              {at(shown.finished_at)} · took {elapsed(shown.started_at, shown.finished_at)}
            </span>
          </div>
          {shown.windows_total > 1 && shown.windows_done < shown.windows_total && (
            <p className="sy-detail">
              Read {shown.windows_done} of {shown.windows_total} months before it
              stopped. Those months are written and kept — running it again carries
              on from there rather than starting over.
            </p>
          )}
          {shown.error && (
            <div className="sy-error">
              <div className="sy-error-h">
                {status === "PARTIAL" ? "Why it stopped" : "Why it failed"}
              </div>
              <p>{shown.error}</p>
              {status === "PARTIAL" && (
                <p className="sy-quiet">
                  The rows it did write were kept. Running it again carries on from
                  there rather than starting over.
                </p>
              )}
            </div>
          )}
          <Summary run={shown} />
          {canSync && (status === "FAILED" || status === "PARTIAL") && onRetry && (
            <button className="btn btn-secondary btn-sm" onClick={onRetry}>
              Try again
            </button>
          )}
        </>
      ) : (
        <p className="sy-detail">
          Nothing has been pulled yet. A first sync reads the books from the date you
          choose and can take several minutes.
        </p>
      )}

      {note && <div className="sy-note">{note}</div>}
      {error && <div className="sy-error"><p>{error}</p></div>}
    </Bp>
  );
}
