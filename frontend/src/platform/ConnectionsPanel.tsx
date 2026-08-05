import { useCallback, useEffect, useRef, useState } from "react";
import { since, todayISO } from "../when";
import { papi } from "./api";
import type {
  ConnectionCheck,
  ConnectionsView,
  NewConnectionInput,
  PlatformSession,
  ZohoConnection,
  ZohoVisibleOrg,
} from "./types";
import { Bp, Labelled, Tip } from "./ui";

/**
 * Zoho connections — as many companies as the business has books to read.
 *
 * The screen this replaces could hold exactly one Zoho company per platform
 * organization, which meant a business with three legal entities needed three
 * separate logins to look at three sets of books. Removing that restriction
 * makes three things worth saying on screen rather than leaving them to be
 * discovered:
 *
 * **Which company is broken.** "The organization is connected" stops meaning
 * anything once there are three and one has a revoked token. Health is per
 * connection, with when it was last checked — a connection that has never been
 * checked and one that failed an hour ago look identical otherwise, and only
 * one of them is a problem.
 *
 * **Scopes.** A half-granted scope is the most common reason a connection
 * authenticates and then returns nothing: the token works, one endpoint 401s,
 * and the sync reports zero rows with no visible cause. They are listed with
 * what each one buys, so the failure is diagnosable before it happens.
 *
 * **What pooling costs.** Rows from every enabled connection on an
 * organization are analysed together — revenue and margin roll up across all
 * of them. That is the right default for one business with several books and
 * the wrong one for entities that must stay apart, so it is stated up front.
 */

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

const EMPTY_FORM = {
  zoho_organization_id: "",
  label: "",
  client_id: "",
  client_secret: "",
  refresh_token: "",
  accounts_base: DC_PRESETS[0].accounts_base,
  api_base: DC_PRESETS[0].api_base,
};

// "never", not "—": a connector that has never been checked is a different
// state from a missing field, and only one of them is a problem.
function when(iso: string | null | undefined): string {
  return iso ? since(iso) : "never";
}

function health(c: ZohoConnection): { tone: "ok" | "bad" | "unknown" | "off"; label: string } {
  if (!c.enabled) return { tone: "off", label: "Paused" };
  if (c.last_check_ok === true) return { tone: "ok", label: "Reachable" };
  if (c.last_check_ok === false) return { tone: "bad", label: "Not reachable" };
  return { tone: "unknown", label: "Not checked" };
}

/* ── one connection ───────────────────────────────────────────────────────── */

function ConnectionCard({
  conn,
  canManage,
  canSync,
  onCheck,
  onRename,
  onToggle,
  onDelete,
  onSync,
  syncing,
  syncBusy,
}: {
  conn: ZohoConnection;
  canManage: boolean;
  canSync: boolean;
  onCheck: (id: string) => Promise<ConnectionCheck | null>;
  onRename: (id: string, label: string) => Promise<void>;
  onToggle: (id: string, enabled: boolean) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onSync: (id: string, since: string, full: boolean) => Promise<void>;
  /** This card's own company is the one being pulled. */
  syncing: boolean;
  /** Some sync is running — starting a second would pull the same books twice. */
  syncBusy: boolean;
}) {
  const [renaming, setRenaming] = useState(false);
  const [label, setLabel] = useState(conn.label);
  const [busy, setBusy] = useState(false);
  const [check, setCheck] = useState<ConnectionCheck | null>(null);
  // Seeded from what this company was last read from, so a repeat pull offers
  // the window that was already chosen for it rather than a global default.
  const [since, setSince] = useState(conn.suggested_since);
  const [full, setFull] = useState(false);

  useEffect(() => setLabel(conn.label), [conn.label]);
  useEffect(() => setSince(conn.suggested_since), [conn.suggested_since]);

  const h = health(conn);

  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    try {
      await fn();
    } finally {
      setBusy(false);
    }
  }

  return (
    <Bp className={`cx-card ${h.tone}`}>
      <div className="cx-head">
        <div className="cx-name">
          {renaming ? (
            <>
              <input
                className="input"
                value={label}
                aria-label="Connection name"
                autoFocus
                onChange={(e) => setLabel(e.target.value)}
              />
              <button
                className="btn btn-primary btn-sm"
                disabled={busy}
                onClick={() => run(async () => {
                  await onRename(conn.connection_id, label.trim());
                  setRenaming(false);
                })}
              >
                Save
              </button>
              <button className="btn btn-ghost btn-sm" onClick={() => { setLabel(conn.label); setRenaming(false); }}>
                Cancel
              </button>
            </>
          ) : (
            <>
              <h4>{conn.label}</h4>
              {canManage && (
                <button className="btn btn-ghost btn-sm" onClick={() => setRenaming(true)}>
                  Rename
                </button>
              )}
            </>
          )}
        </div>
        <span className={`cx-badge ${h.tone}`}>{h.label}</span>
      </div>

      <dl className="cx-facts">
        <div>
          <dt>
            <Labelled tip="The company id inside Zoho Books, from Settings → Organization Profile or the id in its URL. It is a request parameter on every call, not part of the login.">
              Zoho company
            </Labelled>
          </dt>
          <dd className="mono">{conn.zoho_organization_id}</dd>
        </div>
        <div>
          <dt>
            <Labelled tip="The OAuth grant used to reach it. A refresh token belongs to a Zoho user, not a company, so one grant usually serves every company that user can see — and rotating it once covers all of them.">
              Sign-in used
            </Labelled>
          </dt>
          <dd>
            {conn.credential_label}
            {conn.credential_rotated_at && (
              <div className="st-help">rotated {conn.credential_rotated_at.slice(0, 10)}</div>
            )}
          </dd>
        </div>
        <div>
          <dt>
            <Labelled tip="A refresh token issued in one Zoho data centre is rejected by every other. A mismatch here is the single most common setup failure.">
              Data centre
            </Labelled>
          </dt>
          <dd>
            {DC_PRESETS.find((p) => p.accounts_base === conn.accounts_base)?.label ??
              conn.accounts_base.replace("https://accounts.", "")}
          </dd>
        </div>
        <div>
          <dt>
            <Labelled tip="When this connection was last asked whether it still works. A connection that has never been checked and one that failed an hour ago look identical without this.">
              Last checked
            </Labelled>
          </dt>
          <dd>{when(conn.last_checked_at)}</dd>
        </div>
      </dl>

      {(check?.detail ?? conn.last_check_detail) && (
        <p className={`cx-detail ${h.tone === "bad" ? "bad" : ""}`}>
          {check?.detail ?? conn.last_check_detail}
        </p>
      )}

      {check?.visible_organizations && check.visible_organizations.length > 0 && (
        <>
          <p className="cx-detail">
            This sign-in also reaches{" "}
            {check.visible_organizations.length === 1 ? "this company only" : "these companies"}:
          </p>
          <ul className="cred-orgs">
            {check.visible_organizations.map((o) => (
              <li key={o.organization_id}>
                <span className="cred-org" style={{ display: "block" }}>
                  {o.name} <span className="mono">{o.organization_id}</span>
                  {o.organization_id === conn.zoho_organization_id && <em> this one</em>}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}

      {/* The date belongs next to the button that uses it. It used to live in
          a panel further down the page, so pressing "Pull from this company"
          read as a pull with no date at all — and the date it silently used
          was whatever had been typed for a different company. */}
      {canSync && (
        <div className="cx-pull">
          {/* The date and the button go when a company is paused, because a
              paused company is not pulled. What it last brought in does not —
              that is history, and it is the thing you check before deciding
              whether to resume it. */}
          {conn.enabled && (
            <>
          <label htmlFor={`cx-since-${conn.connection_id}`}>
            <Labelled
              tip={
                <>
                  Every invoice and bill dated after this is fetched individually, so an
                  earlier date means a longer pull. The detectors compare the last 90 days
                  against the 90 before that and need six months of history before they
                  will call a decline.
                  {conn.last_sync?.since && (
                    <> This company was last read from {conn.last_sync.since}.</>
                  )}
                </>
              }
            >
              Read this company's books from
            </Labelled>
          </label>
          <input
            id={`cx-since-${conn.connection_id}`}
            type="date"
            className="input"
            value={since}
            max={todayISO()}
            onChange={(e) => setSince(e.target.value)}
          />
          <label className="sync-check">
            <input type="checkbox" checked={full} onChange={(e) => setFull(e.target.checked)} />
            Re-read documents already held
            <Tip text="A repeat pull normally skips documents it already holds, which is what makes it fast. Tick this after granting a scope that was missing — the documents are there, but the fields that scope unlocks are not." />
          </label>
            </>
          )}
          {conn.last_sync ? (
            <div className="cx-lastpull">
              Last pulled {when(conn.last_sync.started_at)} from {conn.last_sync.since ?? "a rolling window"} ·{" "}
              {conn.last_sync.sales_txns} sales lines, {conn.last_sync.cost_records} cost records
              {conn.last_sync.status !== "OK" && <> · {conn.last_sync.status.toLowerCase()}</>}
              {conn.last_sync.error && <div className="cx-detail bad">{conn.last_sync.error}</div>}
            </div>
          ) : (
            <div className="cx-lastpull">This company has never been pulled on its own.</div>
          )}
        </div>
      )}

      <div className="cx-actions">
        {canManage && (
          <button
            className="btn btn-secondary btn-sm"
            disabled={busy}
            onClick={() => run(async () => setCheck(await onCheck(conn.connection_id)))}
          >
            {busy ? "Checking…" : "Check"}
          </button>
        )}
        {canSync && conn.enabled && (
          <button
            className="btn btn-primary btn-sm"
            disabled={syncBusy}
            onClick={() => onSync(conn.connection_id, since, full)}
          >
            {syncing ? "Pulling this one…" : syncBusy ? "A sync is running" : "Pull from this company"}
          </button>
        )}
        <span className="spacer" />
        {canManage && (
          <>
            <button
              className="btn btn-ghost btn-sm"
              disabled={busy}
              onClick={() => run(() => onToggle(conn.connection_id, !conn.enabled))}
            >
              {conn.enabled ? "Pause" : "Resume"}
            </button>
            <Tip
              text={
                conn.enabled
                  ? "Pausing stops this company feeding the analysis and stops it being pulled, without touching the credentials or anything already synced. Reversible."
                  : "Resuming puts this company back into the pooled analysis. Its previously synced rows were never removed, so they return with it."
              }
            />
            <button
              className="btn btn-ghost btn-sm"
              disabled={busy}
              onClick={() => run(() => onDelete(conn.connection_id))}
            >
              Remove
            </button>
            <Tip text="Drops the credentials for this company and stops pulling it. Rows already synced from it stay — they are facts about what was traded, and this is a decision about access, not about history." />
          </>
        )}
      </div>
    </Bp>
  );
}

/* ── adding one ───────────────────────────────────────────────────────────── */

function AddConnection({
  view,
  token,
  onAdded,
}: {
  view: ConnectionsView;
  token: string;
  onAdded: () => Promise<void>;
}) {
  const hasCredentials = view.credentials.length > 0;
  const [mode, setMode] = useState<"existing" | "new">(hasCredentials ? "existing" : "new");
  const [form, setForm] = useState(EMPTY_FORM);
  const [credentialId, setCredentialId] = useState(view.credentials[0]?.credential_id ?? "");
  const [orgs, setOrgs] = useState<ZohoVisibleOrg[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Adding the *first* company creates the first sign-in, and the second
  // company should then reuse it — that is the whole point of separating the
  // two. Left alone, this form stayed on the fresh-secrets tab with a stale
  // empty credential id, so adding a second company either did nothing or was
  // rejected for secrets it was never asked for.
  const hadCredentials = useRef(hasCredentials);
  useEffect(() => {
    if (!hasCredentials) {
      setMode("new");
      setCredentialId("");
      hadCredentials.current = false;
      return;
    }
    if (!hadCredentials.current) {
      // First sign-in just arrived: switch to reuse and forget the secrets.
      setMode("existing");
      setForm(EMPTY_FORM);
      hadCredentials.current = true;
    }
    if (!view.credentials.some((c) => c.credential_id === credentialId)) {
      setCredentialId(view.credentials[0].credential_id);
    }
  }, [hasCredentials, view.credentials, credentialId]);

  async function listCompanies() {
    setError(null);
    setOrgs(null);
    try {
      const r = await papi.credentialOrganizations(token, credentialId);
      setOrgs(r.visible_organizations);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const body: NewConnectionInput =
        mode === "existing"
          ? {
              credential_id: credentialId,
              zoho_organization_id: form.zoho_organization_id.trim(),
              label: form.label.trim(),
            }
          : {
              zoho_organization_id: form.zoho_organization_id.trim(),
              label: form.label.trim(),
              client_id: form.client_id.trim(),
              client_secret: form.client_secret.trim(),
              refresh_token: form.refresh_token.trim(),
              accounts_base: form.accounts_base,
              api_base: form.api_base,
            };
      await papi.addConnection(token, body);
      setForm(EMPTY_FORM);
      setOrgs(null);
      await onAdded();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Bp className="st-section cx-add">
      <h3>
        <Labelled tip="Each company you add is one Zoho Books organization. Adding a second does not create a second tenant here — the rows land together in this organization's analysis.">
          Add a company
        </Labelled>
      </h3>

      {hasCredentials && (
        <div className="cx-tabs">
          <button
            type="button"
            className="cx-tab"
            aria-pressed={mode === "existing"}
            onClick={() => setMode("existing")}
          >
            Use a sign-in already on file
          </button>
          <button
            type="button"
            className="cx-tab"
            aria-pressed={mode === "new"}
            onClick={() => setMode("new")}
          >
            Enter a different Zoho sign-in
          </button>
        </div>
      )}

      <form onSubmit={submit}>
        {mode === "existing" ? (
          <>
            <p className="st-help">
              The normal path for a second or third company. One Zoho sign-in already
              reaches every company that user can see, so re-entering the same secret
              would only create a copy for a future rotation to miss.
            </p>
            <label htmlFor="cx-cred" style={{ marginTop: 10 }}>
              Zoho sign-in
            </label>
            <select
              id="cx-cred"
              className="input"
              value={credentialId}
              onChange={(e) => {
                setCredentialId(e.target.value);
                setOrgs(null);
              }}
            >
              {view.credentials.map((c) => (
                <option key={c.credential_id} value={c.credential_id}>
                  {c.label} · {c.client_id.slice(0, 18)}… · used by {c.used_by}{" "}
                  {c.used_by === 1 ? "company" : "companies"}
                </option>
              ))}
            </select>

            <button
              type="button"
              className="btn btn-ghost btn-sm"
              style={{ marginTop: 8 }}
              disabled={!credentialId}
              onClick={listCompanies}
            >
              Show the companies this reaches
            </button>
            {orgs && (
              <ul className="cred-orgs">
                {orgs.length === 0 && <li className="st-help">Zoho returned no companies for this sign-in.</li>}
                {orgs.map((o) => (
                  <li key={o.organization_id}>
                    <button
                      type="button"
                      className="cred-org"
                      disabled={o.already_connected}
                      onClick={() =>
                        setForm((f) => ({
                          ...f,
                          zoho_organization_id: o.organization_id,
                          label: f.label || o.name,
                        }))
                      }
                    >
                      {o.name} <span className="mono">{o.organization_id}</span>
                      {o.already_connected && <em> already added</em>}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </>
        ) : (
          <>
            <label htmlFor="cx-dc">
              <Labelled tip="Must match the account the token was issued from. A token from accounts.zoho.in is rejected by accounts.zoho.com with an error that reads like a bad secret.">
                Data centre
              </Labelled>
            </label>
            <select
              id="cx-dc"
              className="input"
              value={form.accounts_base}
              onChange={(e) => {
                const p = DC_PRESETS.find((d) => d.accounts_base === e.target.value);
                if (p) setForm({ ...form, accounts_base: p.accounts_base, api_base: p.api_base });
              }}
            >
              {DC_PRESETS.map((p) => (
                <option key={p.accounts_base} value={p.accounts_base}>
                  {p.label}
                </option>
              ))}
            </select>

            <label htmlFor="cx-client-id" style={{ marginTop: 10 }}>Client ID</label>
            <input
              id="cx-client-id"
              className="input"
              required
              value={form.client_id}
              onChange={(e) => setForm({ ...form, client_id: e.target.value })}
            />

            <label htmlFor="cx-client-secret" style={{ marginTop: 10 }}>Client secret</label>
            <input
              id="cx-client-secret"
              type="password"
              className="input"
              required
              value={form.client_secret}
              onChange={(e) => setForm({ ...form, client_secret: e.target.value })}
            />

            <label htmlFor="cx-refresh" style={{ marginTop: 10 }}>
              <Labelled tip="Encrypted before it is stored and never shown again. Generate it in the Zoho API console with the scopes listed below — a token missing one of them authenticates and then returns nothing.">
                Refresh token
              </Labelled>
            </label>
            <input
              id="cx-refresh"
              type="password"
              className="input"
              required
              value={form.refresh_token}
              onChange={(e) => setForm({ ...form, refresh_token: e.target.value })}
            />
          </>
        )}

        <label htmlFor="cx-zoho-org" style={{ marginTop: 10 }}>
          <Labelled tip="Settings → Organization Profile in Zoho Books, or the id in its URL. Not the same as this platform's organization.">
            Zoho Books organization id
          </Labelled>
        </label>
        <input
          id="cx-zoho-org"
          className="input"
          required
          value={form.zoho_organization_id}
          onChange={(e) => setForm({ ...form, zoho_organization_id: e.target.value })}
        />

        <label htmlFor="cx-label" style={{ marginTop: 10 }}>
          Name it
          <span className="fsrc">
            What you call this entity — "4U Precision", not "60036630626". A list of three
            numbers is unreadable at the moment you need it.
          </span>
        </label>
        <input
          id="cx-label"
          className="input"
          value={form.label}
          onChange={(e) => setForm({ ...form, label: e.target.value })}
        />

        {error && <p className="cx-detail bad">{error}</p>}
        <div style={{ marginTop: 12 }}>
          <button className="btn btn-primary btn-sm" disabled={busy}>
            {busy ? "Adding…" : "Add company"}
          </button>
        </div>
      </form>
    </Bp>
  );
}

/* ── scopes ───────────────────────────────────────────────────────────────── */

function Scopes({ view }: { view: ConnectionsView }) {
  const [copied, setCopied] = useState(false);
  return (
    <Bp className="st-section">
      <h3>
        <Labelled tip="Zoho grants scopes individually. A token missing one still authenticates — the failing endpoint returns 401 and the sync reports zero rows for that kind of record with nothing obviously wrong.">
          Scopes this platform needs
        </Labelled>
      </h3>
      <p className="st-help">
        Paste this into the scope field when you generate the token in the Zoho API
        console. Granting fewer does not fail loudly; it fails quietly, later.
      </p>
      <table className="cx-scopes">
        <tbody>
          {view.required_scopes.map((s) => (
            <tr key={s.scope}>
              <td className="mono">{s.scope}</td>
              <td>{s.why}</td>
              <td className="req">{s.required ? "required" : "optional"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="cx-scopestring">
        <code>{view.scope_string}</code>
        <button
          className="btn btn-ghost btn-sm"
          onClick={() => {
            navigator.clipboard?.writeText(view.scope_string);
            setCopied(true);
            window.setTimeout(() => setCopied(false), 2000);
          }}
        >
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
    </Bp>
  );
}

/* ── the panel ────────────────────────────────────────────────────────────── */

export function ConnectionsPanel({
  session,
  canSync,
  onSync,
  syncBusy,
  activeConnectionId,
}: {
  session: PlatformSession;
  canSync: boolean;
  /** Runs a pull for one company, from the date that company's card chose. */
  onSync: (connectionId: string, since: string, full: boolean) => Promise<void>;
  /** True while any sync is in flight — one job at a time per organization. */
  syncBusy: boolean;
  /** The company the running job is pulling, when it named one. */
  activeConnectionId: string | null;
}) {
  const [view, setView] = useState<ConnectionsView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setView(await papi.listConnections(session.token));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [session.token]);

  useEffect(() => {
    load();
  }, [load]);

  async function check(id: string): Promise<ConnectionCheck | null> {
    try {
      const r = await papi.checkConnection(session.token, id);
      await load();
      return r;
    } catch (e) {
      setError((e as Error).message);
      return null;
    }
  }

  async function rename(id: string, label: string) {
    try {
      await papi.editConnection(session.token, id, { label });
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function toggle(id: string, enabled: boolean) {
    try {
      await papi.editConnection(session.token, id, { enabled });
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function remove(id: string) {
    const c = view?.connections.find((x) => x.connection_id === id);
    if (
      !window.confirm(
        `Remove "${c?.label ?? id}"?\n\n` +
          "The credentials for this company are dropped and it stops being pulled. " +
          "Invoices, bills and margins already synced from it stay — they are facts " +
          "about what was traded, and disconnecting is about access, not history.\n\n" +
          "They will keep feeding this organization's totals until you delete the " +
          "organization itself.",
      )
    ) {
      return;
    }
    try {
      const r = await papi.removeConnection(session.token, id);
      setNote(r.note);
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (!view) {
    return error ? (
      <div className="state-panel">
        <div className="state-mark">Could not read the connections</div>
        <p style={{ margin: 0, fontSize: 13.5 }}>{error}</p>
      </div>
    ) : (
      <div className="skeleton" style={{ height: 90 }} />
    );
  }

  const enabled = view.connections.filter((c) => c.enabled).length;

  return (
    <>
      <div className="dp-screen-head">
        <div>
          <h2>
            <Labelled tip="One company here is one Zoho Books organization. Add as many as the business keeps books for — the rows land together in this organization's analysis.">
              Companies
            </Labelled>
          </h2>
          <p className="text-muted">
            {[
              view.connections.length === 0
                ? "None connected yet"
                : `${view.connections.length} connected · ${enabled} feeding the analysis`,
              view.source_mode !== "api" ? "running against the offline sample source" : null,
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>
      </div>

      {view.connections.length > 1 && <div className="cx-pool">{view.pooling_note}</div>}
      {error && <div className="dp-error">{error}</div>}
      {note && (
        <div className="cx-pool" style={{ background: "var(--color-accent-100)", color: "var(--color-accent-800)", borderLeftColor: "var(--color-accent)" }}>
          {note}
          <button className="btn btn-ghost btn-sm" onClick={() => setNote(null)}>Dismiss</button>
        </div>
      )}

      <div className="cx-list">
        {view.connections.map((c) => (
          <ConnectionCard
            key={c.connection_id}
            conn={c}
            canManage={view.can_manage}
            canSync={canSync}
            onCheck={check}
            onRename={rename}
            onToggle={toggle}
            onDelete={remove}
            onSync={async (id, since, full) => {
              await onSync(id, since, full);
              await load();   // last pulled / suggested date move with the run
            }}
            syncing={activeConnectionId === c.connection_id}
            syncBusy={syncBusy}
          />
        ))}
        {view.connections.length === 0 && (
          <Bp className="cx-empty">
            {view.can_manage
              ? "No Zoho company is connected, so every screen is showing sample data or nothing at all. Add one below."
              : "No Zoho company is connected. Ask an owner to add one."}
          </Bp>
        )}
      </div>

      {view.can_manage && <AddConnection view={view} token={session.token} onAdded={load} />}
      {view.can_manage && <Scopes view={view} />}
    </>
  );
}
