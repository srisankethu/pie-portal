import { useCallback, useEffect, useMemo, useState } from "react";
import {
  DEMO_ACCOUNTS,
  clearPlatformSession,
  loadPlatformSession,
  papi,
  savePlatformSession,
} from "./api";
import type { DecisionDetail, DecisionSummary, PlatformSession, Role } from "./types";
import { aiState, factLabel, factValue, isPrimaryFact } from "./format";
import { Bp, Conf, FactChip, Interpretation, Pri, typeLabel } from "./ui";

type Screen = "home" | "list" | "detail" | "customer" | "quotes" | "states";

const ROLE_HOME: Record<Role, { title: string; sub: string; nav: string }> = {
  SALESPERSON: { title: "Today", sub: "Decisions that need you, most urgent first", nav: "Today" },
  SALES_MANAGER: {
    title: "Team focus",
    sub: "Where the team's attention is worth spending, and what is waiting on you",
    nav: "Team focus",
  },
  OWNER: { title: "Where to intervene", sub: "The commercial situations that deserve a decision", nav: "Where to intervene" },
};

// ── sign in ──────────────────────────────────────────────────────────────────
function SignIn({ onIn }: { onIn: (s: PlatformSession) => void }) {
  const [email, setEmail] = useState("r.nair@sanketh.in");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    if (!password) return setErr("Enter your password.");
    setBusy(true);
    try {
      const r = await papi.login(email, password);
      onIn({ token: r.token, role: r.role, name: r.name, user_id: r.user_id, organization_id: r.organization_id });
    } catch (e2) {
      setErr((e2 as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="signin-wrap">
      <form className="signin" onSubmit={submit}>
        <h6 className="text-muted">Sanketh</h6>
        <h2>Commercial Decisions</h2>
        <p className="text-muted" style={{ marginBottom: "var(--space-6)" }}>
          One product, three doors. Your account decides what you see first and what you may act on.
        </p>
        <div className="field" style={{ marginBottom: "var(--space-3)" }}>
          <label>Email</label>
          <input className="input" value={email} autoFocus onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div className="field">
          <label>Password</label>
          <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        {err && <div className="err">{err}</div>}
        <button className="btn btn-primary" style={{ width: "100%", marginTop: "var(--space-4)" }} disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <div className="demo">
          Demo — <b>r.nair@sanketh.in</b> salesperson · <b>m.rao@sanketh.in</b> manager ·{" "}
          <b>s.menon@sanketh.in</b> owner. Any password.
        </div>
      </form>
    </div>
  );
}

// ── decision card (home) ─────────────────────────────────────────────────────
function DecisionCard({
  d,
  onOpen,
  onAct,
}: {
  d: DecisionDetail;
  onOpen: () => void;
  onAct: (kind: string) => void;
}) {
  const state = aiState(d.interpretation.status);
  const chips = d.facts.filter((f) => !f.restricted && isPrimaryFact(f.label)).slice(0, 3);
  return (
    <Bp className="dcard">
      <div className="dcard-top">
        <span className="dcard-type">{typeLabel(d.decision_type)}</span>
        <Pri band={d.priority.band} />
        <span className="dp-spacer" />
        <button className="btn btn-ghost btn-sm" onClick={onOpen}>
          Open evidence →
        </button>
      </div>
      <div className="dcard-cust">{d.subject_label}</div>
      {d.interpretation.explanation && <div className="dcard-reason">{d.interpretation.explanation}</div>}
      {chips.length > 0 && (
        <div className="dcard-chips">
          {chips.map((f) => (
            <FactChip key={f.label} f={f} />
          ))}
        </div>
      )}
      {state === "ok" || state === "degraded" ? (
        <div className="interp" style={{ marginBottom: 12 }}>
          <div className="interp-mark">AI recommendation{state === "degraded" ? " · degraded" : ""}</div>
          <p className="rec">{d.interpretation.recommendation || d.interpretation.explanation}</p>
        </div>
      ) : (
        <div className="state-panel" style={{ marginBottom: 12 }}>
          <div className="state-mark">{state === "failed" ? "Interpretation unavailable" : "Recommendation withheld"}</div>
          <p style={{ margin: 0, fontSize: 13.5 }}>
            {state === "failed"
              ? "Facts are present; the reading is not. You can still act."
              : "Shown, but no recommendation is offered on this evidence."}
          </p>
        </div>
      )}
      <div className="dcard-actions">
        {state === "ok" && (
          <button className="btn btn-primary btn-sm" onClick={() => onAct("accept")}>
            Accept recommendation
          </button>
        )}
        <button className="btn btn-secondary btn-sm" onClick={() => onAct("modify")}>
          Do something different
        </button>
        <button className="btn btn-ghost btn-sm" onClick={() => onAct("dismiss")}>
          Dismiss
        </button>
        <span className="dp-spacer" />
        <Conf level={d.confidence?.evidence_sufficiency} />
      </div>
    </Bp>
  );
}

// ── action modal ─────────────────────────────────────────────────────────────
const ACTION_META: Record<string, { title: string; body: string; api: string; needsNote?: boolean }> = {
  accept: { title: "Accept and act", body: "The recommendation is logged against this decision. Nothing is sent to the customer.", api: "ACT" },
  modify: { title: "Do something different", body: "Record what you will actually do — the difference from the recommendation is the most useful feedback the system gets.", api: "ACT", needsNote: true },
  dismiss: { title: "Dismiss this decision", body: "Tell us why, so the same thing is not raised again next week.", api: "DISMISS", needsNote: true },
  escalate: { title: "Send to management", body: "Routed to someone who can see the full economics and approve a price. They receive the facts, the interpretation and your note.", api: "OVERRIDE" },
};

function ActionModal({
  kind,
  onClose,
  onConfirm,
  busy,
}: {
  kind: string;
  onClose: () => void;
  onConfirm: (note: string) => void;
  busy: boolean;
}) {
  const meta = ACTION_META[kind];
  const [note, setNote] = useState("");
  return (
    <div className="dp-modal-back" onClick={onClose}>
      <Bp className="dp-modal" style={{ background: "var(--color-bg)" }}>
        <div onClick={(e) => e.stopPropagation()}>
          <div className="kicker">{kind.toUpperCase()}</div>
          <h3>{meta.title}</h3>
          <p className="text-muted" style={{ fontSize: 13.5 }}>
            {meta.body}
          </p>
          <div className="field">
            <label>{meta.needsNote ? "What you will do / why" : "Anything to add (optional)"}</label>
            <textarea
              className="input"
              style={{ minHeight: 80 }}
              value={note}
              autoFocus
              onChange={(e) => setNote(e.target.value)}
              placeholder="Kept on the decision. Not sent to the customer."
            />
          </div>
          <div className="foot">
            <button className="btn btn-secondary" onClick={onClose}>
              Cancel
            </button>
            <button
              className="btn btn-primary"
              disabled={busy || (meta.needsNote && !note.trim())}
              onClick={() => onConfirm(note)}
            >
              {busy ? "Logging…" : "Log decision"}
            </button>
          </div>
        </div>
      </Bp>
    </div>
  );
}

// ── main ─────────────────────────────────────────────────────────────────────
export default function PlatformApp({ onOpenQuotes }: { onOpenQuotes: () => void }) {
  const [session, setSession] = useState<PlatformSession | null>(loadPlatformSession());
  const [screen, setScreen] = useState<Screen>("home");
  const [summaries, setSummaries] = useState<DecisionSummary[] | null>(null);
  const [details, setDetails] = useState<Record<string, DecisionDetail>>({});
  const [detailId, setDetailId] = useState<string | null>(null);
  const [customerId, setCustomerId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [listType, setListType] = useState("");
  const [toast, setToast] = useState<string | null>(null);
  const [modal, setModal] = useState<{ id: string; kind: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const flash = (m: string) => {
    setToast(m);
    setTimeout(() => setToast(null), 3500);
  };

  const load = useCallback(async () => {
    if (!session) return;
    setLoading(true);
    setError(null);
    try {
      const list = await papi.listDecisions(session.token);
      setSummaries(list);
      const entries = await Promise.all(
        list.map(async (s) => [s.decision_id, await papi.getDetail(session.token, s.decision_id)] as const),
      );
      setDetails(Object.fromEntries(entries));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [session]);

  useEffect(() => {
    if (session) load();
  }, [session, load]);

  const signIn = (s: PlatformSession) => {
    savePlatformSession(s);
    setSession(s);
    setScreen("home");
  };
  const signOut = () => {
    clearPlatformSession();
    setSession(null);
    setSummaries(null);
    setDetails({});
  };
  const switchRole = async (email: string) => {
    try {
      const r = await papi.login(email, "demo");
      signIn({ token: r.token, role: r.role, name: r.name, user_id: r.user_id, organization_id: r.organization_id });
    } catch (e) {
      flash((e as Error).message);
    }
  };

  const openDetail = (id: string) => {
    setDetailId(id);
    setScreen("detail");
  };

  const doAction = async (id: string, kind: string, note: string) => {
    if (!session) return;
    setBusy(true);
    try {
      const meta = ACTION_META[kind];
      await papi.act(session.token, id, { action: meta.api, note, reason: kind === "dismiss" ? note : undefined });
      setModal(null);
      flash(`${meta.title} · logged`);
      const d = await papi.getDetail(session.token, id);
      setDetails((m) => ({ ...m, [id]: d }));
      const list = await papi.listDecisions(session.token);
      setSummaries(list);
    } catch (e) {
      flash((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const openDecisions = useMemo(
    () => (summaries || []).filter((s) => s.status === "OPEN" || s.status === "VIEWED"),
    [summaries],
  );

  if (!session) return <SignIn onIn={signIn} />;

  const rh = ROLE_HOME[session.role];
  const roleShort = session.role === "SALESPERSON" ? "Salesperson" : session.role === "SALES_MANAGER" ? "Manager" : "Owner";
  const navItems: [Screen, string, string][] = [
    ["home", rh.nav, session.role === "SALESPERSON" ? String(openDecisions.length) : ""],
    ["list", "Decisions", summaries ? String(summaries.length) : ""],
    ["customer", "Accounts", ""],
    ["quotes", "Quotes", ""],
    ["states", "Data & AI states", ""],
  ];

  return (
    <div className="dp">
      <div className="dp-top">
        <span className="dp-brand">Sanketh · Decisions</span>
        <div className="dp-nav">
          {navItems.map(([key, label, count]) => (
            <button
              key={key}
              className={screen === key || (key === "list" && screen === "detail") ? "on" : ""}
              onClick={() => setScreen(key)}
            >
              {label}
              {count && <span className="n-count">{count}</span>}
            </button>
          ))}
        </div>
        <span className="dp-spacer" />
        <div className="dp-role" role="group" aria-label="Switch role (demo)">
          {DEMO_ACCOUNTS.map((a) => (
            <button key={a.role} className={session.role === a.role ? "on" : ""} onClick={() => switchRole(a.email)}>
              {a.label}
            </button>
          ))}
        </div>
        <div className="dp-whoami">
          <b>{session.name}</b>
          <br />
          {roleShort}
        </div>
        <button className="btn btn-secondary btn-sm" onClick={signOut}>
          Sign out
        </button>
      </div>

      <div className="dp-main">
        {error && <div className="dp-error">Could not load decisions: {error}</div>}

        {/* ── HOME (role-aware) ── */}
        {screen === "home" && (
          <>
            <div className="dp-head">
              <h1>{rh.title}</h1>
              <p>{rh.sub}</p>
            </div>
            {loading && !summaries ? (
              <>
                <div className="skeleton" />
                <div className="skeleton" />
              </>
            ) : openDecisions.length === 0 ? (
              <Bp style={{ padding: 40, textAlign: "center" }}>
                <div className="dp-empty" style={{ padding: 0 }}>
                  Nothing needs a decision right now. When the data raises something, it appears here — newest first.
                </div>
              </Bp>
            ) : (
              <>
                <div className="dp-count">
                  {openDecisions.length} open {openDecisions.length === 1 ? "decision" : "decisions"} · {roleShort} view
                </div>
                <div className="dp-cards">
                  {openDecisions.map((s) => {
                    const d = details[s.decision_id];
                    if (!d) return <div className="skeleton" key={s.decision_id} />;
                    return (
                      <DecisionCard
                        key={s.decision_id}
                        d={d}
                        onOpen={() => openDetail(s.decision_id)}
                        onAct={(kind) => setModal({ id: s.decision_id, kind })}
                      />
                    );
                  })}
                </div>
              </>
            )}
          </>
        )}

        {/* ── DECISION LIST ── */}
        {screen === "list" && (
          <ListScreen
            summaries={summaries}
            details={details}
            loading={loading}
            listType={listType}
            setListType={setListType}
            onOpen={openDetail}
          />
        )}

        {/* ── DETAIL ── */}
        {screen === "detail" && detailId && (
          <DetailScreen
            d={details[detailId]}
            loading={loading}
            onBack={() => setScreen("list")}
            onAct={(kind) => setModal({ id: detailId, kind })}
            onOpenAccount={(cid) => {
              setCustomerId(cid);
              setScreen("customer");
            }}
          />
        )}

        {/* ── ACCOUNTS (customer intelligence, light) ── */}
        {screen === "customer" && (
          <CustomerScreen details={details} customerId={customerId} setCustomerId={setCustomerId} onOpen={openDetail} />
        )}

        {/* ── QUOTES (integration surface) ── */}
        {screen === "quotes" && (
          <div>
            <div className="dp-head">
              <h1>Quote intelligence</h1>
              <p>Verified context and a role-gated economics view while you price a line.</p>
            </div>
            <Bp style={{ padding: 22, maxWidth: 640 }}>
              <p style={{ marginTop: 0 }}>
                Quote context resolves the requested item, shows this customer's own price history and — for
                managers — the cost and margin, then leaves the price in your hands. It never pre-fills the field.
              </p>
              <button className="btn btn-primary" onClick={onOpenQuotes}>
                Open the Quote Builder →
              </button>
            </Bp>
          </div>
        )}

        {/* ── DATA & AI STATES (reference) ── */}
        {screen === "states" && <StatesScreen />}
      </div>

      {modal && (
        <ActionModal
          kind={modal.kind}
          busy={busy}
          onClose={() => setModal(null)}
          onConfirm={(note) => doAction(modal.id, modal.kind, note)}
        />
      )}
      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}

// ── list screen ──────────────────────────────────────────────────────────────
function ListScreen({
  summaries,
  details,
  loading,
  listType,
  setListType,
  onOpen,
}: {
  summaries: DecisionSummary[] | null;
  details: Record<string, DecisionDetail>;
  loading: boolean;
  listType: string;
  setListType: (t: string) => void;
  onOpen: (id: string) => void;
}) {
  const types = ["", "CUSTOMER_DECLINE", "CUSTOMER_DORMANCY", "MARGIN_DETERIORATION", "COST_PASS_THROUGH", "QUOTE_CONTEXT"];
  const rows = (summaries || []).filter((s) => !listType || s.decision_type === listType);
  return (
    <div>
      <div className="dp-head">
        <h1>Decisions</h1>
        <p>Every decision raised, open and closed — sorted by priority then recency.</p>
      </div>
      <div style={{ display: "flex", gap: 8, marginBottom: 14, flexWrap: "wrap" }}>
        {types.map((t) => (
          <button
            key={t || "all"}
            className={`btn btn-sm ${listType === t ? "btn-primary" : "btn-secondary"}`}
            onClick={() => setListType(t)}
          >
            {t ? typeLabel(t) : "All"}
          </button>
        ))}
      </div>
      {loading && !summaries ? (
        <>
          <div className="skeleton" style={{ height: 44 }} />
          <div className="skeleton" style={{ height: 44 }} />
        </>
      ) : rows.length === 0 ? (
        <div className="dp-empty">No decisions match this filter.</div>
      ) : (
        <Bp style={{ padding: 2 }}>
          <table className="dp-table">
            <thead>
              <tr>
                <th>Priority</th>
                <th>Type</th>
                <th>Account / subject</th>
                <th>Why</th>
                <th>Confidence</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => {
                const d = details[s.decision_id];
                return (
                  <tr key={s.decision_id} data-open onClick={() => onOpen(s.decision_id)}>
                    <td>
                      <Pri band={s.priority_band} />
                    </td>
                    <td style={{ fontFamily: "var(--font-heading)" }}>{typeLabel(s.decision_type)}</td>
                    <td>{d ? d.subject_label : s.subject_entity_id}</td>
                    <td className="dp-reason-cell">
                      <div className="trunc" style={{ maxWidth: "38ch" }}>
                        {d?.interpretation.explanation || "—"}
                      </div>
                    </td>
                    <td>{d ? <Conf level={d.confidence?.evidence_sufficiency} /> : "—"}</td>
                    <td style={{ fontSize: 12 }}>{s.status}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Bp>
      )}
    </div>
  );
}

// ── detail screen ────────────────────────────────────────────────────────────
function DetailScreen({
  d,
  loading,
  onBack,
  onAct,
  onOpenAccount,
}: {
  d: DecisionDetail | undefined;
  loading: boolean;
  onBack: () => void;
  onAct: (kind: string) => void;
  onOpenAccount: (cid: string) => void;
}) {
  if (loading && !d) return <div className="dp-loading">Loading decision…</div>;
  if (!d) return <div className="dp-empty">Decision not found in this view.</div>;
  const state = aiState(d.interpretation.status);
  const closed = d.status !== "OPEN" && d.status !== "VIEWED";
  return (
    <div>
      <button className="btn btn-ghost btn-sm" onClick={onBack} style={{ marginBottom: 10 }}>
        ← All decisions
      </button>
      <div className="dcard-top" style={{ marginBottom: 4 }}>
        <span className="dcard-type">{typeLabel(d.decision_type)}</span>
        <Pri band={d.priority.band} />
        {closed && <span className="pri LOW">{d.status}</span>}
      </div>
      <h1 style={{ margin: "2px 0 18px" }}>{d.subject_label}</h1>

      <div className="dp-split">
        {/* LEFT — facts */}
        <div>
          <div className="facts-mark">Facts · what the data shows</div>
          {d.facts.length === 0 ? (
            <div className="dp-empty" style={{ padding: 16, textAlign: "left" }}>
              No numeric facts are exposed at your permission level for this decision.
            </div>
          ) : (
            <Bp style={{ padding: "8px 14px" }}>
              <table className="facttable">
                <tbody>
                  {d.facts.filter((f) => isPrimaryFact(f.label)).map((f) => (
                    <tr key={f.label}>
                      <td>
                        {factLabel(f.label)}
                        <div className="fsrc">{f.source}</div>
                      </td>
                      <td className="fv">{factValue(f.label, f.value)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Bp>
          )}

          <div className="section-h">Evidence used</div>
          {d.evidence.length === 0 ? (
            <div className="text-muted" style={{ fontSize: 13 }}>
              Source records are recorded with the signal.
            </div>
          ) : (
            groupEvidence(d.evidence).map((e, i) => (
              <div className="evi" key={i}>
                <span>{e.system}</span>
                <span className="text-muted">{e.count}</span>
              </div>
            ))
          )}

          {d.human_action && (
            <>
              <div className="section-h">Human log</div>
              <div className="evi">
                <span>
                  {d.human_action.action} · {d.human_action.note || "no note"}
                </span>
                <span className="text-muted">{new Date(d.human_action.acted_at).toLocaleDateString("en-IN")}</span>
              </div>
            </>
          )}
        </div>

        {/* RIGHT — interpretation + action */}
        <div>
          <Interpretation d={d} />
          <div style={{ display: "flex", gap: 8, alignItems: "center", margin: "10px 0 4px" }}>
            <Conf level={d.confidence?.evidence_sufficiency} />
            <span className="text-muted" style={{ fontSize: 11 }}>
              priority {d.priority.score}/100 · base {d.priority.deterministic_base}
              {d.priority.ai_adjustment ? ` · ai ${d.priority.ai_adjustment > 0 ? "+" : ""}${d.priority.ai_adjustment}` : ""}
            </span>
          </div>

          {!closed && (
            <div className="action-panel">
              {state === "ok" && (
                <button className="btn btn-primary" onClick={() => onAct("accept")}>
                  Accept the recommendation
                </button>
              )}
              <button className="btn btn-secondary" onClick={() => onAct("modify")}>
                Do something different
              </button>
              <div style={{ display: "flex", gap: 8 }}>
                <button className="btn btn-ghost btn-sm" onClick={() => onAct("dismiss")}>
                  Dismiss with reason
                </button>
                <button className="btn btn-ghost btn-sm" onClick={() => onAct("escalate")}>
                  Escalate to management
                </button>
              </div>
              <button
                className="btn btn-ghost btn-sm"
                style={{ alignSelf: "flex-start" }}
                onClick={() => onOpenAccount(d.subject_entity_id)}
              >
                Open the account →
              </button>
            </div>
          )}
          {closed && (
            <div className="outcome">
              <div className="outcome-mark">Closed</div>
              <p style={{ margin: 0, fontSize: 13.5 }}>
                This decision was {d.status.toLowerCase()}. Outcome measurement runs on later Zoho data and will
                appear here when available.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── customer intelligence (light) ────────────────────────────────────────────
function CustomerScreen({
  details,
  customerId,
  setCustomerId,
  onOpen,
}: {
  details: Record<string, DecisionDetail>;
  customerId: string | null;
  setCustomerId: (id: string | null) => void;
  onOpen: (id: string) => void;
}) {
  const all = Object.values(details);
  const accounts = useMemo(() => {
    const m = new Map<string, string>();
    all.forEach((d) => {
      if (d.subject_entity_type === "CUSTOMER") m.set(d.subject_entity_id, d.subject_label);
    });
    return [...m.entries()];
  }, [all]);

  if (!customerId) {
    return (
      <div>
        <div className="dp-head">
          <h1>Account intelligence</h1>
          <p>Pick an account to see its open decisions and the facts behind them.</p>
        </div>
        {accounts.length === 0 ? (
          <div className="dp-empty">No customer-level decisions are visible at your permission level.</div>
        ) : (
          <div className="dp-cards">
            {accounts.map(([id, name]) => (
              <Bp className="dcard" key={id}>
                <div className="dcard-cust">{name}</div>
                <button className="btn btn-ghost btn-sm" onClick={() => setCustomerId(id)}>
                  Open account →
                </button>
              </Bp>
            ))}
          </div>
        )}
      </div>
    );
  }
  const decs = all.filter((d) => d.subject_entity_id === customerId);
  const name = decs[0]?.subject_label || customerId;
  return (
    <div>
      <button className="btn btn-ghost btn-sm" onClick={() => setCustomerId(null)} style={{ marginBottom: 10 }}>
        ← All accounts
      </button>
      <div className="dp-head">
        <h1>{name}</h1>
        <p>Trading facts and what we read from them.</p>
      </div>
      <div className="dp-count">{decs.length} open {decs.length === 1 ? "decision" : "decisions"} on this account</div>
      <div className="dp-cards">
        {decs.map((d) => (
          <Bp className="dcard" key={d.decision_id}>
            <div className="dcard-top">
              <span className="dcard-type">{typeLabel(d.decision_type)}</span>
              <Pri band={d.priority.band} />
              <span className="dp-spacer" />
              <button className="btn btn-ghost btn-sm" onClick={() => onOpen(d.decision_id)}>
                Open →
              </button>
            </div>
            {d.interpretation.explanation && <div className="dcard-reason">{d.interpretation.explanation}</div>}
            <div className="dcard-chips">
              {d.facts.filter((f) => !f.restricted && isPrimaryFact(f.label)).slice(0, 4).map((f) => (
                <FactChip key={f.label} f={f} />
              ))}
            </div>
          </Bp>
        ))}
      </div>
    </div>
  );
}

// ── data & AI states reference ───────────────────────────────────────────────
function StatesScreen() {
  const items = [
    ["AI unavailable", "The reasoning service is down. Facts, history and evidence are read straight from your systems and shown; only the reading is missing. You can still act."],
    ["Recommendation withheld", "Evidence is insufficient or the situation should not be acted on blindly. The movement is shown; a judgement is withheld rather than manufactured."],
    ["Interpretation degraded", "The model responded but failed validation (a number it could not support). The deterministic signal is shown instead — never a hedged guess."],
    ["Restricted data", "Cost and margin are absent for a salesperson — removed before they reach the interpretation, the API and this screen."],
  ];
  return (
    <div>
      <div className="dp-head">
        <h1>Data &amp; AI states</h1>
        <p>How the product behaves when it does not know — a designed state, never a spinner in place of an answer.</p>
      </div>
      <div className="dp-cards">
        {items.map(([h, b]) => (
          <div className="state-panel" key={h}>
            <div className="state-mark">{h}</div>
            <p style={{ margin: 0, fontSize: 14 }}>{b}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

function groupEvidence(evi: { source_system?: string; record_type?: string }[]) {
  const m = new Map<string, number>();
  evi.forEach((e) => {
    const k = `${e.source_system || "zoho"} · ${e.record_type || "record"}`;
    m.set(k, (m.get(k) || 0) + 1);
  });
  return [...m.entries()].map(([system, n]) => ({ system, count: `${n} record${n === 1 ? "" : "s"}` }));
}
