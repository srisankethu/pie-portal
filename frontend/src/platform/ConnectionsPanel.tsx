import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";
import { useCallback, useEffect, useRef, useState } from "react";
import Alert from "@mui/material/Alert";
import { formatDate, since, todayISO } from "../when";
import { papi } from "./api";
import { ErrorState, LoadingState } from "./kit";
import type {
  ConnectionCheck,
  ConnectionsView,
  ConnectorCatalogEntry,
  ConnectorField,
  ErpDiscoveredCompany,
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

// `code` is the short data-centre token the OAuth endpoint expects on
// `?dc=` (in / com / eu / com.au / jp) — it maps that to the accounts and API
// bases server-side. The `accounts_base` URL is what the *manual-credentials*
// form stores on a connection. These are two different values, and sending the
// URL where the code was wanted is what produced "Unknown data centre:
// https://accounts.zoho.in": the endpoint had no key by that name.
const DC_PRESETS: { label: string; code: string; accounts_base: string; api_base: string }[] = [
  { label: "India (.in)", code: "in", accounts_base: "https://accounts.zoho.in",
    api_base: "https://www.zohoapis.in/books/v3" },
  { label: "United States (.com)", code: "com", accounts_base: "https://accounts.zoho.com",
    api_base: "https://www.zohoapis.com/books/v3" },
  { label: "Europe (.eu)", code: "eu", accounts_base: "https://accounts.zoho.eu",
    api_base: "https://www.zohoapis.eu/books/v3" },
  { label: "Australia (.com.au)", code: "com.au", accounts_base: "https://accounts.zoho.com.au",
    api_base: "https://www.zohoapis.com.au/books/v3" },
  { label: "Japan (.jp)", code: "jp", accounts_base: "https://accounts.zoho.jp",
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

// "Check failed" rather than "Not reachable" for the stored false, because
// there are now two ways to earn it and they send you to different places: a
// dead credential, or a login that reaches the company perfectly and was never
// granted a scope the pull needs. Calling the second one unreachable points at
// the one thing that is demonstrably fine. A live check knows which it was and
// says so; the stored row only knows that something failed, and the detail line
// under the chip carries the specifics either way.
function health(c: ZohoConnection, check?: ConnectionCheck | null):
  { tone: "ok" | "bad" | "unknown" | "off"; label: string } {
  if (!c.enabled) return { tone: "off", label: "Paused" };
  if (check?.missing_required_scopes?.length) {
    return { tone: "bad", label: "Missing permissions" };
  }
  if (c.last_check_ok === true) return { tone: "ok", label: "Reachable" };
  if (c.last_check_ok === false) return { tone: "bad", label: "Check failed" };
  return { tone: "unknown", label: "Not checked" };
}

/* ── one connection ───────────────────────────────────────────────────────── */

function ConnectionCard({
  conn,
  catalogEntry,
  canManage,
  canSync,
  onCheck,
  onRename,
  onToggle,
  onDelete,
  onRotate,
  onErpRotate,
  onSync,
  syncing,
  syncBusy,
}: {
  conn: ZohoConnection;
  /** The connector's catalog entry — the field list a non-Zoho rotation
   *  renders from. Undefined for Zoho, whose rotation is the token flow. */
  catalogEntry?: ConnectorCatalogEntry;
  canManage: boolean;
  canSync: boolean;
  onCheck: (id: string) => Promise<ConnectionCheck | null>;
  onRename: (id: string, label: string) => Promise<void>;
  onToggle: (id: string, enabled: boolean) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onRotate: (id: string, token: string,
             client?: { client_id: string; client_secret: string }) => Promise<string>;
  onErpRotate: (id: string, values: Record<string, string>) => Promise<string>;
  onSync: (id: string, since: string, full: boolean) => Promise<void>;
  /** This card's own company is the one being pulled.
   *
   *  Only this company's own job blocks this button. The flag it replaces meant
   *  "some sync is running", which disabled all three companies the moment any
   *  one of them started — and made the per-connection concurrency the server
   *  grew unreachable from the only screen that would have used it. Two
   *  connected Zoho companies are two independent pulls against two different
   *  APIs. */
  syncing: boolean;
  /** A start request has been posted and not yet answered. Briefly true for
   *  every card, because until the server replies the screen does not know
   *  which company the click was for. */
  syncBusy: boolean;
}) {
  // Older cached payloads may predate the discriminator; a row with none is a
  // Zoho row, because Zoho rows are the only ones that can predate it.
  const isZoho = (conn.connector ?? "zoho") === "zoho";
  const [renaming, setRenaming] = useState(false);
  const [label, setLabel] = useState(conn.label);
  const [busy, setBusy] = useState(false);
  const [check, setCheck] = useState<ConnectionCheck | null>(null);
  // Seeded from what this company was last read from, so a repeat pull offers
  // the window that was already chosen for it rather than a global default.
  const [since, setSince] = useState(conn.suggested_since);
  const [full, setFull] = useState(false);
  // Rotation is a property of *this connection's* Zoho sign-in, so it lives on
  // this card rather than on a credentials panel elsewhere on the page. It is
  // closed by default: a token box permanently open on a working connection
  // invites somebody to paste into it.
  const [rotating, setRotating] = useState(false);
  const [newToken, setNewToken] = useState("");
  const [rotateNote, setRotateNote] = useState<string | null>(null);
  // The client pair, and whether it is being replaced too. Closed by default
  // for the reason the token box is: a rotation normally happens under the same
  // app, and two secret boxes standing open invite somebody to re-type a secret
  // that was already correct. But it has to be *reachable*, because the one
  // failure a token-only rotation causes is a token generated under a different
  // Self Client — Zoho answers that with `invalid_client_secret`, and until
  // this existed the screen's only remedy was the one that could not fix it.
  const [newClient, setNewClient] = useState({ client_id: "", client_secret: "" });
  const [replacingClient, setReplacingClient] = useState(false);

  useEffect(() => setLabel(conn.label), [conn.label]);
  useEffect(() => setSince(conn.suggested_since), [conn.suggested_since]);

  const h = health(conn, check);

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
              <Button
                variant="contained" size="small"
                disabled={busy}
                onClick={() => run(async () => {
                  await onRename(conn.connection_id, label.trim());
                  setRenaming(false);
                })}
              >
                Save
              </Button>
              <Button variant="text" size="small" onClick={() => { setLabel(conn.label); setRenaming(false); }}>
                Cancel
              </Button>
            </>
          ) : (
            <>
              <h4>{conn.label}</h4>
              {canManage && (
                <Button variant="text" size="small" onClick={() => setRenaming(true)}>
                  Rename
                </Button>
              )}
            </>
          )}
        </div>
        <div className="cx-headright">
          {/* Which system this book lives in. With every company on one
              connector this repeats itself, but this is the screen where the
              distinction is managed, so here it is information. */}
          <Chip size="small" variant="outlined"
                label={conn.connector_label ?? "Zoho Books"} />
          <span className={`cx-badge ${h.tone}`}>{h.label}</span>
        </div>
      </div>

      <dl className="cx-facts">
        <div>
          <dt>
            <Labelled tip={isZoho
              ? "The company id inside Zoho Books, from Settings → Organization Profile or the id in its URL. It is a request parameter on every call, not part of the login."
              : `The ${catalogEntry?.company_term ?? "company"} this connection reads, by the id ${conn.connector_label ?? "the source system"} knows it under.`}>
              {isZoho ? "Zoho company"
                      : `${conn.connector_label ?? "Source"} ${catalogEntry?.company_term ?? "company"}`}
            </Labelled>
          </dt>
          <dd className="mono">{conn.zoho_organization_id}</dd>
        </div>
        <div>
          <dt>
            <Labelled tip={isZoho
              ? "The OAuth grant used to reach it. A refresh token belongs to a Zoho user, not a company, so one grant usually serves every company that user can see — and rotating it once covers all of them."
              : "The stored sign-in used to reach it. One sign-in can serve several companies, and replacing it once covers all of them."}>
              Sign-in used
            </Labelled>
          </dt>
          <dd>
            {conn.credential_label}
            {conn.credential_rotated_at && (
              <div className="st-help">
                rotated {formatDate(conn.credential_rotated_at)}
              </div>
            )}
            {canManage && !rotating && (
              <Button variant="text" size="small" className="cx-rotate-open"
                      onClick={() => { setRotating(true); setRotateNote(null); }}>
                {isZoho ? "Replace the token" : "Replace the sign-in"}
              </Button>
            )}
          </dd>
        </div>
        {isZoho ? (
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
        ) : (
          conn.config && Object.keys(conn.config).length > 0 && (
            <div>
              <dt>
                <Labelled tip="The non-secret settings this connection was entered with. Secrets are encrypted at rest and never shown again.">
                  Settings
                </Labelled>
              </dt>
              <dd className="mono">
                {Object.entries(conn.config)
                  .map(([k, v]) => `${k}: ${v}`).join(" · ")}
              </dd>
            </div>
          )
        )}
        <div>
          <dt>
            <Labelled tip="When this connection was last asked whether it still works. A connection that has never been checked and one that failed an hour ago look identical without this.">
              Last checked
            </Labelled>
          </dt>
          <dd>{when(conn.last_checked_at)}</dd>
        </div>
      </dl>

      {rotating && !isZoho && catalogEntry && (
        <ErpRotateForm
          entry={catalogEntry}
          credentialLabel={conn.credential_label}
          busy={busy}
          onCancel={() => setRotating(false)}
          onSubmit={(values) => run(async () => {
            const note = await onErpRotate(conn.connection_id, values);
            setRotating(false);
            setRotateNote(note);
          })}
        />
      )}

      {rotating && isZoho && (
        <div className="cx-rotate">
          <label htmlFor={`cx-token-${conn.connection_id}`}>
            <Labelled tip="Generate a fresh refresh token in the Zoho API console for the same client, then paste it here. The client id and secret are left alone by default, because re-typing a secret that is already correct is how a working connection gets broken — but if the token came from a different app, replace them too or Zoho refuses the pair.">
              New refresh token
            </Labelled>
          </label>
          <input
            id={`cx-token-${conn.connection_id}`}
            className="input"
            value={newToken}
            autoComplete="off"
            spellCheck={false}
            placeholder="1000.xxxxxxxx.xxxxxxxx"
            onChange={(e) => setNewToken(e.target.value)}
          />
          {/* Reachable, not open. A token generated under a *different* Zoho
              app is the one failure a token-only rotation produces, and Zoho
              reports it as `invalid_client_secret` — which reads as "your
              secret is wrong" and sends people to change the data centre, the
              one setting that was right. */}
          {replacingClient ? (
            <>
              <label htmlFor={`cx-rcid-${conn.connection_id}`} style={{ marginTop: 10 }}>
                <Labelled tip="Only when the token came from a different app in the Zoho API console. A client keeps one id across data centres but has a separate secret in each, so copy both from the console for this connection's data centre.">
                  Client ID
                </Labelled>
              </label>
              <input
                id={`cx-rcid-${conn.connection_id}`}
                className="input"
                autoComplete="off"
                spellCheck={false}
                value={newClient.client_id}
                onChange={(e) => setNewClient({ ...newClient, client_id: e.target.value })}
              />
              <label htmlFor={`cx-rcs-${conn.connection_id}`} style={{ marginTop: 10 }}>
                Client secret
              </label>
              <input
                id={`cx-rcs-${conn.connection_id}`}
                type="password"
                className="input"
                autoComplete="off"
                value={newClient.client_secret}
                onChange={(e) => setNewClient({ ...newClient, client_secret: e.target.value })}
              />
            </>
          ) : (
            <Button variant="text" size="small"
                    onClick={() => setReplacingClient(true)}>
              The token came from a different app — replace the client id and secret too
            </Button>
          )}
          {/* Said before it happens, not after. One Zoho grant usually reaches
              every company its user can see, so rotating from here rotates
              those too — which is the point, and a surprise if unstated. */}
          <p className="st-help">
            This replaces the sign-in for every company using{" "}
            <strong>{conn.credential_label}</strong>, not only this one. The
            connection is re-checked immediately afterwards.
          </p>
          <div className="cx-rotate-actions">
            <Button variant="contained" size="small"
                    disabled={busy || !newToken.trim() || (replacingClient
                      && !(newClient.client_id.trim() && newClient.client_secret.trim()))}
                    onClick={() => run(async () => {
                      // Sent only when both are filled in: a half-supplied pair
                      // would replace one side of a matched credential and
                      // break a connection that was merely being re-tokened.
                      const note = await onRotate(
                        conn.connection_id, newToken.trim(),
                        replacingClient
                          ? { client_id: newClient.client_id.trim(),
                              client_secret: newClient.client_secret.trim() }
                          : undefined);
                      setNewToken("");
                      setNewClient({ client_id: "", client_secret: "" });
                      setReplacingClient(false);
                      setRotating(false);
                      setRotateNote(note);
                    })}>
              {busy ? "Rotating…" : "Rotate"}
            </Button>
            <Button variant="text" size="small"
                    onClick={() => {
                      setRotating(false);
                      setNewToken("");
                      setNewClient({ client_id: "", client_secret: "" });
                      setReplacingClient(false);
                    }}>
              Cancel
            </Button>
          </div>
        </div>
      )}
      {rotateNote && <p className="cx-detail">{rotateNote}</p>}

      {(check?.detail ?? conn.last_check_detail) && (
        <p className={`cx-detail ${h.tone === "bad" ? "bad" : ""}`}>
          {check?.detail ?? conn.last_check_detail}
        </p>
      )}

      {/* Only the gaps. A list of ten green ticks is noise on a healthy
          connection, and it buries the one line that needs acting on. */}
      {check?.scopes && check.scopes.some((s) => s.granted !== true) && (
        <ul className="cx-scopegaps">
          {check.scopes.filter((s) => s.granted !== true).map((s) => (
            <li key={s.scope} className={s.granted === false ? "bad" : ""}>
              <code>{s.scope}</code>{" "}
              {s.granted === false
                ? (check.missing_required_scopes?.includes(s.scope)
                    ? "refused — no sync can run until this is granted"
                    : "refused — what it reads stays empty")
                : "could not be tested, so this says nothing either way"}
            </li>
          ))}
        </ul>
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
                  {conn.covered_from && (
                    <> This company has been read from {conn.covered_from}{" "}
                      onwards. An earlier date reads the months in between for
                      the first time.</>
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
          {/* What this company actually holds, and what the chosen date will
              cost. "Last pulled from 2025-01-01" answers neither: a nightly
              pull can run for a year and still cover only the window the first
              run asked for.

              The second line is the one that matters. Widening the window used
              to be a silent no-op — the run went green and fetched nothing —
              so the screen now says, before the button is pressed, which of the
              two pulls is about to happen. */}
          <p className="cx-detail">
            {conn.covered_from ? (
              <>Read from <strong>{formatDate(conn.covered_from)}</strong> onwards
                so far.{" "}
                {since < conn.covered_from
                  ? <>This date reaches further back, so those extra months are
                      listed in full — slower than a repeat pull.</>
                  : <>This date is inside that, so the pull only picks up what
                      has changed.</>}
              </>
            ) : (
              <>Nothing has been read from this company yet, so this first pull
                lists everything from the date above.</>
            )}
          </p>
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
          <Button
            variant="outlined" size="small"
            disabled={busy}
            onClick={() => run(async () => setCheck(await onCheck(conn.connection_id)))}
          >
            {busy ? "Checking…" : "Check"}
          </Button>
        )}
        {canSync && conn.enabled && (
          <Button
            variant="contained" size="small"
            disabled={syncing || syncBusy}
            onClick={() => onSync(conn.connection_id, since, full)}
          >
            {syncing ? "Pulling this one…" : syncBusy ? "Starting…" : "Pull from this company"}
          </Button>
        )}
        <span className="spacer" />
        {canManage && (
          <>
            <Button
              variant="text" size="small"
              disabled={busy}
              onClick={() => run(() => onToggle(conn.connection_id, !conn.enabled))}
            >
              {conn.enabled ? "Pause" : "Resume"}
            </Button>
            <Tip
              text={
                conn.enabled
                  ? "Pausing stops this company feeding the analysis and stops it being pulled, without touching the credentials or anything already synced. Reversible."
                  : "Resuming puts this company back into the pooled analysis. Its previously synced rows were never removed, so they return with it."
              }
            />
            <Button
              variant="text" size="small"
              disabled={busy}
              onClick={() => run(() => onDelete(conn.connection_id))}
            >
              Remove
            </Button>
            <Tip text="Drops the credentials for this company and stops pulling it. Rows already synced from it stay — they are facts about what was traded, and this is a decision about access, not about history." />
          </>
        )}
      </div>
    </Bp>
  );
}

/* ── registered ERP connectors (NetSuite, Business Central, Acumatica, P21,
      Sage) ─────────────────────────────────────────────────────────────────
   These forms render from the catalog's field specs, so this file never
   hardcodes what one system needs — connector number seven appears in the
   picker the day its backend module registers. Zoho keeps its richer bespoke
   flow (OAuth, data centres, scope probing) below. */

function FieldInput({
  field,
  idPrefix,
  value,
  onChange,
}: {
  field: ConnectorField;
  idPrefix: string;
  value: string;
  onChange: (v: string) => void;
}) {
  const id = `${idPrefix}-${field.name}`;
  return (
    <>
      <label htmlFor={id} style={{ marginTop: 10 }}>
        {field.help
          ? <Labelled tip={field.help}>{field.label}</Labelled>
          : field.label}
        {!field.required && <span className="st-help"> (optional)</span>}
      </label>
      <input
        id={id}
        className="input"
        type={field.secret ? "password" : "text"}
        autoComplete="off"
        spellCheck={false}
        required={field.required}
        placeholder={field.placeholder || undefined}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </>
  );
}

function ErpRotateForm({
  entry,
  credentialLabel,
  busy,
  onCancel,
  onSubmit,
}: {
  entry: ConnectorCatalogEntry;
  credentialLabel: string;
  busy: boolean;
  onCancel: () => void;
  onSubmit: (values: Record<string, string>) => void;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const ready = entry.credential_fields.every(
    (f) => !f.required || (values[f.name] ?? "").trim() !== "");
  return (
    <div className="cx-rotate">
      <p className="st-help">
        Enter the fresh sign-in for {entry.label}. All of it — a half-replaced
        credential is how a working connection gets broken.
      </p>
      {entry.credential_fields.map((f) => (
        <FieldInput key={f.name} field={f} idPrefix="cx-erp-rotate"
                    value={values[f.name] ?? ""}
                    onChange={(v) => setValues((s) => ({ ...s, [f.name]: v }))} />
      ))}
      <p className="st-help">
        This replaces the sign-in for every company using{" "}
        <strong>{credentialLabel}</strong>, not only this one. The connection
        is re-checked immediately afterwards.
      </p>
      <div className="cx-rotate-actions">
        <Button variant="contained" size="small" disabled={busy || !ready}
                onClick={() => onSubmit(values)}>
          {busy ? "Rotating…" : "Rotate"}
        </Button>
        <Button variant="text" size="small" onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  );
}

function ErpConnectForm({
  entry,
  token,
  onAdded,
}: {
  entry: ConnectorCatalogEntry;
  token: string;
  onAdded: () => Promise<void>;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ConnectionCheck | null>(null);
  const [companies, setCompanies] = useState<ErpDiscoveredCompany[] | null>(null);

  // Discovery signs in with the credential half alone, so it is offered as
  // soon as that half is complete — the company field is what it exists to
  // fill in.
  const credentialReady = entry.credential_fields.every(
    (f) => !f.required || (values[f.name] ?? "").trim() !== "");

  async function discover() {
    setError(null);
    setCompanies(null);
    setBusy(true);
    try {
      const creds = Object.fromEntries(
        entry.credential_fields
          .map((f) => [f.name, (values[f.name] ?? "").trim()])
          .filter(([, v]) => v !== ""));
      const r = await papi.discoverErpCompanies(token, entry.key, creds);
      setCompanies(r.companies);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const entered = Object.fromEntries(
        Object.entries(values)
          .map(([k, v]) => [k, v.trim()])
          .filter(([, v]) => v !== ""));
      const r = await papi.addErpConnection(token, {
        connector: entry.key,
        values: entered,
        label: label.trim(),
      });
      setResult(r);
      setValues({});
      setLabel("");
      setCompanies(null);
      await onAdded();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit}>
      <p className="st-help">{entry.setup_note}</p>

      {entry.credential_fields.map((f) => (
        <FieldInput key={f.name} field={f} idPrefix={`cx-erp-${entry.key}`}
                    value={values[f.name] ?? ""}
                    onChange={(v) => setValues((s) => ({ ...s, [f.name]: v }))} />
      ))}

      {entry.can_discover && (
        <div style={{ marginTop: 8 }}>
          <Button type="button" variant="text" size="small"
                  disabled={busy || !credentialReady}
                  onClick={discover}>
            List the {entry.company_term} choices this sign-in can see
          </Button>
          {companies && (
            <ul className="cred-orgs">
              {companies.length === 0 && (
                <li className="st-help">
                  {entry.label} returned nothing for this sign-in.
                </li>
              )}
              {companies.map((c) => (
                <li key={c.id}>
                  <button type="button" className="cred-org"
                          onClick={() => {
                            setValues((s) => ({
                              ...s, [entry.external_id_field]: c.id }));
                            setLabel((l) => l || c.name);
                          }}>
                    {c.name} <span className="mono">{c.id}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {entry.connection_fields.map((f) => (
        <FieldInput key={f.name} field={f} idPrefix={`cx-erp-${entry.key}`}
                    value={values[f.name] ?? ""}
                    onChange={(v) => setValues((s) => ({ ...s, [f.name]: v }))} />
      ))}

      <label htmlFor={`cx-erp-${entry.key}-label`} style={{ marginTop: 10 }}>
        Name it
        <span className="fsrc">
          What you call this entity — a name, not an id. A list of three ids
          is unreadable at the moment you need it.
        </span>
      </label>
      <input
        id={`cx-erp-${entry.key}-label`}
        className="input"
        value={label}
        onChange={(e) => setLabel(e.target.value)}
      />

      {error && <p className="cx-detail bad">{error}</p>}
      {result && (
        <p className={`cx-detail ${result.ok ? "" : "bad"}`}>
          {result.ok
            ? `Connected. ${result.detail ?? ""}`
            : `Added, but the check failed: ${result.detail ?? "no detail"}. ` +
              "Fix the values and use Replace the sign-in on its card."}
        </p>
      )}
      <div style={{ marginTop: 12 }}>
        <Button type="submit" variant="contained" size="small" disabled={busy}>
          {busy ? "Adding…" : `Add ${entry.company_term}`}
        </Button>
      </div>
    </form>
  );
}

/* ── adding one ───────────────────────────────────────────────────────────── */

function AddConnection({
  view,
  catalog,
  token,
  onAdded,
}: {
  view: ConnectionsView;
  catalog: ConnectorCatalogEntry[];
  token: string;
  onAdded: () => Promise<void>;
}) {
  // Which system the company lives in. Zoho first — it is the platform's
  // richest flow and the incumbent — then every registered connector.
  const [connector, setConnector] = useState("zoho");
  const entry = catalog.find((c) => c.key === connector);
  const hasCredentials = view.credentials.length > 0;
  const [mode, setMode] = useState<"existing" | "new" | "oauth">(hasCredentials ? "existing" : "new");
  const [form, setForm] = useState(EMPTY_FORM);
  const [credentialId, setCredentialId] = useState(view.credentials[0]?.credential_id ?? "");
  const [orgs, setOrgs] = useState<ZohoVisibleOrg[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // OAuth flow state. Holds the data-centre *code* (in / com / …), which is
  // what `authorizeZoho` sends on `?dc=` — not the accounts_base URL.
  const [oauthDc, setOauthDc] = useState<string>(DC_PRESETS[0].code);
  const [oauthAuthUrl, setOauthAuthUrl] = useState<string | null>(null);

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

  async function startOAuth() {
    setBusy(true);
    setError(null);
    try {
      const resp = await papi.authorizeZoho(token, oauthDc);
      setOauthAuthUrl(resp.authorization_url);
      // Store state in sessionStorage for coordination
      sessionStorage.setItem("oauth_state_token", resp.state_token);
      // Open authorization URL in new window
      window.open(resp.authorization_url, '_blank', 'width=800,height=600');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
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
        <Labelled tip="Each company you add is one set of books in its own system — a Zoho Books organization, a NetSuite account, a Business Central company. Adding a second does not create a second tenant here: the rows land together in this organization's analysis.">
          Add a company
        </Labelled>
      </h3>

      {catalog.length > 0 && (
        <div className="cx-tabs" role="group" aria-label="Which system">
          <button type="button" className="cx-tab"
                  aria-pressed={connector === "zoho"}
                  onClick={() => setConnector("zoho")}>
            Zoho Books
          </button>
          {catalog.map((c) => (
            <button key={c.key} type="button" className="cx-tab"
                    aria-pressed={connector === c.key}
                    onClick={() => setConnector(c.key)}>
              {c.label}
            </button>
          ))}
        </div>
      )}

      {connector !== "zoho" && entry && (
        <ErpConnectForm entry={entry} token={token} onAdded={onAdded} />
      )}

      {connector === "zoho" && (
      <>
      <div className="cx-tabs">
        {hasCredentials && (
          <button
            type="button"
            className="cx-tab"
            aria-pressed={mode === "existing"}
            onClick={() => setMode("existing")}
          >
            Use a sign-in already on file
          </button>
        )}
        <button
          type="button"
          className="cx-tab"
          aria-pressed={mode === "oauth"}
          onClick={() => setMode("oauth")}
        >
          Authorize with Zoho
        </button>
        {!hasCredentials && (
          <button
            type="button"
            className="cx-tab"
            aria-pressed={mode === "new"}
            onClick={() => setMode("new")}
          >
            Enter credentials manually
          </button>
        )}
      </div>

      <form onSubmit={submit}>
        {mode === "oauth" ? (
          <>
            {!oauthAuthUrl ? (
              <>
                <p className="st-help">
                  Authorize PIE to access your Zoho Books account. You'll be guided through
                  Zoho's authorization flow, and your credentials will never be shared with PIE.
                </p>
                <label htmlFor="oauth-dc">
                  <Labelled tip="Must match the data centre of your Zoho account. A token from accounts.zoho.in is rejected by accounts.zoho.com.">
                    Data centre
                  </Labelled>
                </label>
                <TextField
                  id="oauth-dc"
                  select
                  fullWidth
                  size="small"
                  value={oauthDc}
                  onChange={(e) => setOauthDc(e.target.value)}
                  sx={{ mt: 1.5, mb: 1.5 }}
                >
                  {DC_PRESETS.map((p) => (
                    <MenuItem key={p.code} value={p.code}>
                      {p.label}
                    </MenuItem>
                  ))}
                </TextField>
                <Button
                  type="button"
                  variant="contained"
                  size="small"
                  disabled={busy}
                  onClick={startOAuth}
                >
                  {busy ? "Starting…" : "Authorize with Zoho"}
                </Button>
              </>
            ) : (
              <>
                <p className="st-help">
                  A browser window opened for Zoho authorization. After you complete the authorization in Zoho,
                  the browser will show your organizations. Copy the credential ID from that page and paste it below,
                  or close that window and try again.
                </p>
                <p className="st-help" style={{ marginTop: 12, fontStyle: "italic" }}>
                  Note: The OAuth connection flow will be completed shortly. For now, please use the manual credentials
                  tab to connect, or refresh this page after authorizing to see your options.
                </p>
                <Button
                  type="button"
                  variant="outlined"
                  size="small"
                  onClick={() => {
                    setOauthAuthUrl(null);
                  }}
                  sx={{ mt: 2 }}
                >
                  Start Over
                </Button>
              </>
            )}
          </>
        ) : mode === "existing" ? (
          <>
            <p className="st-help">
              The normal path for a second or third company. One Zoho sign-in already
              reaches every company that user can see, so re-entering the same secret
              would only create a copy for a future rotation to miss.
            </p>
            <TextField
              id="cx-cred"
              select
              fullWidth
              size="small"
              label="Zoho sign-in"
              sx={{ mt: 1.5 }}
              value={credentialId}
              onChange={(e) => {
                setCredentialId(e.target.value);
                setOrgs(null);
              }}
            >
              {view.credentials.map((c) => (
                <MenuItem key={c.credential_id} value={c.credential_id}>
                  {c.label} · {c.client_id.slice(0, 18)}… · used by {c.used_by}{" "}
                  {c.used_by === 1 ? "company" : "companies"}
                </MenuItem>
              ))}
            </TextField>

            <Button
              type="button"
              variant="text" size="small"
              style={{ marginTop: 8 }}
              disabled={!credentialId}
              onClick={listCompanies}
            >
              Show the companies this reaches
            </Button>
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
            <TextField
              id="cx-dc"
              select
              fullWidth
              size="small"
              value={form.accounts_base}
              onChange={(e) => {
                const p = DC_PRESETS.find((d) => d.accounts_base === e.target.value);
                if (p) setForm({ ...form, accounts_base: p.accounts_base, api_base: p.api_base });
              }}
            >
              {DC_PRESETS.map((p) => (
                <MenuItem key={p.accounts_base} value={p.accounts_base}>
                  {p.label}
                </MenuItem>
              ))}
            </TextField>

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

        {mode !== "oauth" && (
          <>
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
          </>
        )}

        {error && <p className="cx-detail bad">{error}</p>}
        {mode !== "oauth" && (
          <div style={{ marginTop: 12 }}>
            <Button type="submit" variant="contained" size="small" disabled={busy}>
              {busy ? "Adding…" : "Add company"}
            </Button>
          </div>
        )}
      </form>
      </>
      )}
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
        <Button
          variant="text" size="small"
          onClick={() => {
            navigator.clipboard?.writeText(view.scope_string);
            setCopied(true);
            window.setTimeout(() => setCopied(false), 2000);
          }}
        >
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
    </Bp>
  );
}

/* ── the panel ────────────────────────────────────────────────────────────── */

export function ConnectionsPanel({
  session,
  canSync,
  onSync,
  busyConnections,
  everyCompanyBusy = false,
  starting,
}: {
  session: PlatformSession;
  canSync: boolean;
  /** Runs a pull for one company, from the date that company's card chose. */
  onSync: (connectionId: string, since: string, full: boolean) => Promise<void>;
  /** Companies with a pull in flight, from the server. Each card gates on its
   *  own membership here rather than on a single organization-wide flag. */
  busyConnections: string[];
  /** An all-companies run is in flight, which covers every card here. It
   *  reports itself with a NULL connection, so it is never in
   *  `busyConnections` — this is how a card knows it is being pulled anyway. */
  everyCompanyBusy?: boolean;
  /** A start request has been posted and not yet answered. */
  starting: boolean;
}) {
  const [view, setView] = useState<ConnectionsView | null>(null);
  const [catalog, setCatalog] = useState<ConnectorCatalogEntry[]>([]);
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

  // Once, not per load: the catalog is the deployment's connector list and
  // changes with releases, not with clicks. Its failure degrades to a
  // Zoho-only add form rather than taking the whole panel down.
  useEffect(() => {
    papi.connectorCatalog(session.token)
      .then((r) => setCatalog(r.connectors))
      .catch(() => setCatalog([]));
  }, [session.token]);

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

  /** Replace the Zoho grant this connection signs in with.
   *
   *  One call, on the connection, because that is the thing somebody is
   *  looking at when they decide to rotate. The server names every other
   *  company that changed underneath, and that sentence is what comes back. */
  async function rotate(
    id: string, token: string,
    client?: { client_id: string; client_secret: string },
  ): Promise<string> {
    setError(null);
    const r = await papi.rotateConnectionToken(session.token, id, token, client);
    await load();
    return String(r.note ?? "Rotated.");
  }

  /** The registered-connector sibling: the whole credential, from the field
   *  list its catalog entry declares. Same disclosure about shared grants. */
  async function erpRotate(id: string, values: Record<string, string>): Promise<string> {
    setError(null);
    const r = await papi.rotateErpConnection(session.token, id, values);
    await load();
    return String(r.note ?? "Rotated.");
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
      <ErrorState title="Could not read the connections" error={error} onRetry={load} />
    ) : (
      <LoadingState rows={1} height={90} />
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
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {note && (
        <div className="cx-pool" style={{ background: "var(--color-accent-100)", color: "var(--color-accent-800)", borderLeftColor: "var(--color-accent)" }}>
          {note}
          <Button variant="text" size="small" onClick={() => setNote(null)}>Dismiss</Button>
        </div>
      )}

      <div className="cx-list">
        {view.connections.map((c) => (
          <ConnectionCard
            key={c.connection_id}
            conn={c}
            catalogEntry={catalog.find((e) => e.key === c.connector)}
            canManage={view.can_manage}
            canSync={canSync}
            onCheck={check}
            onRotate={rotate}
            onErpRotate={erpRotate}
            onRename={rename}
            onToggle={toggle}
            onDelete={remove}
            onSync={async (id, since, full) => {
              await onSync(id, since, full);
              await load();   // last pulled / suggested date move with the run
            }}
            syncing={busyConnections.includes(c.connection_id) || everyCompanyBusy}
            syncBusy={starting}
          />
        ))}
        {view.connections.length === 0 && (
          <Bp className="cx-empty">
            {view.can_manage
              ? "No company is connected, so every screen is showing sample data or nothing at all. Add one below."
              : "No company is connected. Ask an owner to add one."}
          </Bp>
        )}
      </div>

      {view.can_manage && (
        <AddConnection view={view} catalog={catalog} token={session.token}
                       onAdded={load} />
      )}
      {view.can_manage && <Scopes view={view} />}
    </>
  );
}
