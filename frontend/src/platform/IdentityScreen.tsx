import { useCallback, useEffect, useState } from "react";
import { papi } from "./api";
import type { ConnectorRecord, EntityKind, Identity, IdentityPolicy, IdentitySuggestion } from "./types";
import { Bp, Labelled, Tip } from "./ui";

/**
 * Identity management — which connector records describe the same entity.
 *
 * The platform reads several ERPs at once. The same customer sits in Zoho and
 * in ERPNext under different ids; the same item under different codes. Nothing
 * is merged: each connector stays the source of truth for its own records, and
 * an identity only says which records belong together.
 *
 * That distinction is the whole screen. Every record is shown with the
 * connector that supplied it, its own id, and its own values — so a figure can
 * always be traced back to the system it came from. Reviewing a suggestion
 * means comparing two rows side by side and deciding, which is why the evidence
 * ("GSTIN 29ABCDE1234F1Z5") is shown rather than a confidence score: the value
 * is arguable, a number is not.
 */

const KINDS: { key: EntityKind; label: string; hint: string }[] = [
  { key: "customers", label: "Customers",
    hint: "Matched on GSTIN — a registration issued by the government, not by any ERP." },
  { key: "items", label: "Items",
    hint: "Matched on SKU, after punctuation is normalised away." },
];

function when(iso: string | null | undefined): string {
  if (!iso) return "never";
  const d = new Date(iso);
  const mins = Math.round((Date.now() - d.getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)} h ago`;
  return d.toLocaleDateString("en-IN", { dateStyle: "medium" });
}

/** One connector's record, shown as that connector holds it. */
function RecordRow({
  r,
  kind,
  canManage,
  onUnlink,
  lonely,
}: {
  r: ConnectorRecord;
  kind: EntityKind;
  canManage: boolean;
  onUnlink?: (recordId: string) => void;
  /** The only record on its identity — unlinking it would achieve nothing. */
  lonely: boolean;
}) {
  return (
    <tr>
      <td><span className="id-connector">{r.connector}</span></td>
      <td className="mono">{r.external_id}</td>
      <td>{kind === "customers" ? r.name : r.description}</td>
      <td className="mono">
        {kind === "customers" ? r.gstin || "—" : r.sku || "—"}
      </td>
      <td className="text-muted">{when(r.last_synced_at)}</td>
      {canManage && (
        <td className="id-rowactions">
          {onUnlink && !lonely && (
            <button className="btn btn-ghost btn-sm" onClick={() => onUnlink(r.record_id)}>
              Unlink
            </button>
          )}
        </td>
      )}
    </tr>
  );
}

function IdentityCard({
  identity,
  kind,
  canManage,
  onUnlink,
  onRelabel,
}: {
  identity: Identity;
  kind: EntityKind;
  canManage: boolean;
  onUnlink: (recordId: string) => void;
  onRelabel: (identityId: string, label: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [label, setLabel] = useState(identity.label ?? "");
  const linked = identity.connector_count > 1;

  return (
    <Bp className={`id-card ${linked ? "linked" : ""}`}>
      <div className="id-head">
        <div className="id-name">
          {editing ? (
            <>
              <input className="input" value={label} autoFocus
                     aria-label="Identity name"
                     onChange={(e) => setLabel(e.target.value)} />
              <button className="btn btn-primary btn-sm"
                      onClick={() => { onRelabel(identity.identity_id, label); setEditing(false); }}>
                Save
              </button>
              <button className="btn btn-ghost btn-sm" onClick={() => setEditing(false)}>
                Cancel
              </button>
            </>
          ) : (
            <>
              <h4>{identity.display_name || "(unnamed)"}</h4>
              {!identity.label && (
                <Tip text="Taken from the linked records rather than stored, so no single connector becomes the authority on what this entity is called. Rename it to fix a name of your own." />
              )}
              {canManage && (
                <button className="btn btn-ghost btn-sm" onClick={() => setEditing(true)}>
                  Rename
                </button>
              )}
            </>
          )}
        </div>
        <span className={`id-badge ${linked ? "linked" : "single"}`}>
          {linked
            ? `${identity.connector_count} connectors`
            : `${identity.record_count} record${identity.record_count === 1 ? "" : "s"}`}
        </span>
      </div>

      <table className="id-table">
        <thead>
          <tr>
            <th>
              <Labelled tip="Which system this row came from. It is never rewritten from another connector's values — that is what makes a number on any screen traceable.">
                Connector
              </Labelled>
            </th>
            <th>External ID</th>
            <th>{kind === "customers" ? "Customer name" : "Description"}</th>
            <th>{kind === "customers" ? "GSTIN" : "SKU"}</th>
            <th>Last synced</th>
            {canManage && <th></th>}
          </tr>
        </thead>
        <tbody>
          {identity.records.map((r) => (
            <RecordRow key={r.record_id} r={r} kind={kind} canManage={canManage}
                       onUnlink={onUnlink} lonely={identity.records.length === 1} />
          ))}
        </tbody>
      </table>
    </Bp>
  );
}

function SuggestionCard({
  s,
  kind,
  canManage,
  onDecide,
}: {
  s: IdentitySuggestion;
  kind: EntityKind;
  canManage: boolean;
  onDecide: (id: string, accept: boolean) => void;
}) {
  const incoming = s.incoming;
  const label = (r: ConnectorRecord) =>
    kind === "customers" ? r.name : r.description;
  const key = (r: ConnectorRecord) =>
    kind === "customers" ? r.gstin : r.sku;

  return (
    <Bp className="id-sugg">
      <div className="id-sugg-h">
        Possible existing identity found
        <span className="id-evidence">{s.evidence}</span>
      </div>
      <p className="st-help">
        Matched on {s.strategy}. Nothing has been linked — this is a proposal, and
        the two records below stay exactly as their connectors hold them either way.
      </p>

      <div className="id-compare">
        <div>
          <div className="id-side">Just imported</div>
          <div className="id-side-name">{label(incoming) || "(no name)"}</div>
          <dl>
            <div><dt>Connector</dt><dd>{incoming.connector}</dd></div>
            <div><dt>External ID</dt><dd className="mono">{incoming.external_id}</dd></div>
            <div>
              <dt>{kind === "customers" ? "GSTIN" : "SKU"}</dt>
              <dd className="mono">{key(incoming) || "—"}</dd>
            </div>
          </dl>
        </div>
        <div className="id-compare-arrow" aria-hidden="true">→</div>
        <div>
          <div className="id-side">Existing identity</div>
          <div className="id-side-name">{s.target.display_name || "(unnamed)"}</div>
          <dl>
            {s.target.records.map((r) => (
              <div key={r.record_id}>
                <dt>{r.connector}</dt>
                <dd className="mono">{r.external_id}</dd>
              </div>
            ))}
            <div>
              <dt>{kind === "customers" ? "GSTIN" : "SKU"}</dt>
              <dd className="mono">{key(s.target.records[0]) || "—"}</dd>
            </div>
          </dl>
        </div>
      </div>

      {canManage ? (
        <div className="id-sugg-actions">
          <button className="btn btn-primary btn-sm"
                  onClick={() => onDecide(s.suggestion_id, true)}>
            Link to this identity
          </button>
          <button className="btn btn-secondary btn-sm"
                  onClick={() => onDecide(s.suggestion_id, false)}>
            Keep them separate
          </button>
          <Tip text="Keeping them separate is recorded too, and the same pair will not be proposed again. Linking can be undone later — the records themselves are never altered either way." />
        </div>
      ) : (
        <p className="st-help">An owner decides these.</p>
      )}
    </Bp>
  );
}

/* ── the screen ─────────────────────────────────────────────────────────── */

export function IdentityScreen({ token }: { token: string }) {
  const [kind, setKind] = useState<EntityKind>("customers");
  const [tab, setTab] = useState<"review" | "all">("review");
  const [identities, setIdentities] = useState<Identity[]>([]);
  const [suggestions, setSuggestions] = useState<IdentitySuggestion[]>([]);
  const [policy, setPolicy] = useState<IdentityPolicy | null>(null);
  const [canManage, setCanManage] = useState(false);
  const [q, setQ] = useState("");
  const [linkedOnly, setLinkedOnly] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [list, sugg, pol] = await Promise.all([
        papi.listIdentities(token, kind, q, linkedOnly),
        papi.listSuggestions(token, kind),
        papi.identityPolicy(token),
      ]);
      setIdentities(list.identities);
      setCanManage(list.can_manage);
      setSuggestions(sugg.suggestions);
      setPolicy(pol);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [token, kind, q, linkedOnly]);

  useEffect(() => {
    load();
  }, [load]);

  async function guard(fn: () => Promise<unknown>) {
    try {
      await fn();
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const autoFlag = kind === "customers" ? "auto_link_customers" : "auto_link_items";

  return (
    <div className="dp-screen">
      <div className="dp-screen-head">
        <div>
          <h2>
            <Labelled
              tip={
                <>
                  Records from different systems are <b>linked, never merged</b>. Each
                  connector stays the source of truth for its own data; an identity
                  only records which rows describe the same business entity, so a
                  figure can always be traced back to the system it came from.
                </>
              }
            >
              Identities
            </Labelled>
          </h2>
          <p className="text-muted">{KINDS.find((k) => k.key === kind)?.hint}</p>
        </div>
        <div className="dp-screen-actions">
          <div className="cx-tabs">
            {KINDS.map((k) => (
              <button key={k.key} type="button" className="cx-tab"
                      aria-pressed={kind === k.key}
                      onClick={() => { setKind(k.key); setTab("review"); }}>
                {k.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {error && <div className="dp-error">{error}</div>}

      <div className="cx-tabs" style={{ marginBottom: 12 }}>
        <button type="button" className="cx-tab" aria-pressed={tab === "review"}
                onClick={() => setTab("review")}>
          To review {suggestions.length > 0 && <b>({suggestions.length})</b>}
        </button>
        <button type="button" className="cx-tab" aria-pressed={tab === "all"}
                onClick={() => setTab("all")}>
          All identities
        </button>
      </div>

      {tab === "review" ? (
        <>
          {policy && (
            <Bp className="st-section">
              <h3>
                <Labelled tip="With this off, a sync that finds an exact match records a suggestion and waits. On, it links without asking — faster, and unrecoverable when the match was a group trading under one registration.">
                  Automatic linking
                </Labelled>
              </h3>
              <label className={`st-switch ${policy.can_manage ? "" : "readonly"}`}>
                <input type="checkbox" checked={Boolean(policy[autoFlag])}
                       disabled={!policy.can_manage}
                       onChange={(e) =>
                         guard(() => papi.updateIdentityPolicy(token,
                           { [autoFlag]: e.target.checked } as Partial<IdentityPolicy>))} />
                <span>
                  <b>Link {kind} automatically on an exact match</b>
                  <span className="st-help">{policy.note}</span>
                </span>
              </label>
            </Bp>
          )}

          {!loading && suggestions.length === 0 && (
            <Bp className="dp-empty">
              <h4>Nothing to review.</h4>
              <p>
                Suggestions appear here when a sync imports a record whose{" "}
                {kind === "customers" ? "GSTIN" : "SKU"} already exists under another
                connector. None means no exact matches were found — not that
                matching is switched off.
              </p>
            </Bp>
          )}

          <div className="id-list">
            {suggestions.map((s) => (
              <SuggestionCard key={s.suggestion_id} s={s} kind={kind}
                              canManage={canManage}
                              onDecide={(id, accept) =>
                                guard(() => papi.decideSuggestion(token, kind, id, accept))} />
            ))}
          </div>
        </>
      ) : (
        <>
          <div className="ci-controls">
            <label>
              Search
              <input className="input" value={q} placeholder="name, id, GSTIN or SKU"
                     onChange={(e) => setQ(e.target.value)} />
            </label>
            <label className="sync-check">
              <input type="checkbox" checked={linkedOnly}
                     onChange={(e) => setLinkedOnly(e.target.checked)} />
              Only entities seen in more than one system
            </label>
          </div>

          {loading && <div className="text-muted">Loading…</div>}
          {!loading && identities.length === 0 && (
            <Bp className="dp-empty">
              <h4>No identities yet.</h4>
              <p>They are created as records arrive — run a sync from Data &amp; connection.</p>
            </Bp>
          )}

          <div className="id-list">
            {identities.map((i) => (
              <IdentityCard key={i.identity_id} identity={i} kind={kind}
                            canManage={canManage}
                            onUnlink={(recordId) =>
                              guard(() => papi.unlinkRecord(token, kind, recordId))}
                            onRelabel={(id, label) =>
                              guard(() => papi.relabelIdentity(token, kind, id, label))} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
