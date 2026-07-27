import { useCallback, useEffect, useMemo, useState } from "react";
import {
  DEMO_ACCOUNTS,
  clearPlatformSession,
  isAuthError,
  loadPlatformSession,
  papi,
  savePlatformSession,
} from "./api";
import type { Account, DecisionDetail, DecisionSummary, PlatformSession, Role } from "./types";
import { aiState, factLabel, factValue, isPrimaryFact } from "./format";
import { Bp, Conf, FactChip, Interpretation, Pri, typeLabel } from "./ui";
import { navigate, parseHash, type Screen } from "./route";

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
function SignIn({ onIn, notice }: { onIn: (s: PlatformSession) => void; notice?: string | null }) {
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
        {notice && <div className="signin-notice">{notice}</div>}
        <div className="field" style={{ marginBottom: "var(--space-3)" }}>
          <label htmlFor="dp-email">Email</label>
          <input
            id="dp-email"
            name="email"
            type="email"
            autoComplete="username"
            className="input"
            value={email}
            autoFocus
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="dp-password">Password</label>
          <input
            id="dp-password"
            name="password"
            className="input"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
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
  const hasRecommendation = state === "ok" && !!d.interpretation.recommendation;
  // On a degraded/failed card the panel below already carries the deterministic
  // sentence; repeating it as the card's summary line printed it twice verbatim.
  const showSummary = state === "ok" && !!d.interpretation.explanation;

  return (
    <Bp
      className="dcard is-open"
      role="button"
      tabIndex={0}
      aria-label={`Open ${typeLabel(d.decision_type)} for ${d.subject_label}`}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen();
        }
      }}
    >
      <div className="dcard-top">
        <span className="dcard-type">{typeLabel(d.decision_type)}</span>
        <Pri band={d.priority.band} />
      </div>
      <div className="dcard-cust">{d.subject_label}</div>
      {showSummary && <div className="dcard-reason">{d.interpretation.explanation}</div>}
      {chips.length > 0 && (
        <div className="dcard-chips">
          {chips.map((f) => (
            <FactChip key={f.label} f={f} />
          ))}
        </div>
      )}
      {state === "ok" ? (
        <div className="interp" style={{ marginBottom: 12 }}>
          <div className="interp-mark">AI recommendation</div>
          <p className="rec">{d.interpretation.recommendation || d.interpretation.explanation}</p>
        </div>
      ) : (
        <div className="state-panel" style={{ marginBottom: 12 }}>
          <div className="state-mark">
            {state === "degraded"
              ? "Deterministic reading · no AI recommendation"
              : state === "failed"
                ? "Interpretation unavailable"
                : "Recommendation withheld"}
          </div>
          <p style={{ margin: 0, fontSize: 13.5 }}>
            {state === "degraded"
              ? d.interpretation.explanation
              : state === "failed"
                ? "Facts are present; the reading is not. You can still act."
                : "Shown, but no recommendation is offered on this evidence."}
          </p>
        </div>
      )}
      {/* Actions live inside a clickable card, so each stops propagation. */}
      <div className="dcard-actions" onClick={(e) => e.stopPropagation()}>
        {hasRecommendation && (
          <button className="btn btn-primary btn-sm" onClick={() => onAct("accept")}>
            Accept recommendation
          </button>
        )}
        <button className="btn btn-secondary btn-sm" onClick={() => onAct("modify")}>
          {hasRecommendation ? "Do something different" : "Record what you did"}
        </button>
        <button className="btn btn-ghost btn-sm" onClick={() => onAct("dismiss")}>
          Dismiss
        </button>
        <button className="btn btn-ghost btn-sm dcard-open" onClick={onOpen}>
          Open evidence →
        </button>
        <span className="dp-spacer" />
        <Conf level={d.confidence?.evidence_sufficiency} aiStatus={d.interpretation.status} />
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

  // Escape closes the dialog — the reflex everyone has, and the only exit for
  // a keyboard user who opened it by mistake.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="dp-modal-back" onClick={onClose}>
      <Bp className="dp-modal" role="dialog" aria-modal="true" aria-label={meta.title}
          style={{ background: "var(--color-bg)" }}>
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
  // Screen lives in the URL hash so Back, reload and shareable links all work.
  const [route, setRoute] = useState(() => parseHash(window.location.hash));
  const screen: Screen = route.screen;
  const [summaries, setSummaries] = useState<DecisionSummary[] | null>(null);
  const [details, setDetails] = useState<Record<string, DecisionDetail>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [listType, setListType] = useState("");
  const [toast, setToast] = useState<{ msg: string; undo?: () => void } | null>(null);
  const [modal, setModal] = useState<{ id: string; kind: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const detailId = route.screen === "detail" ? route.id ?? null : null;
  const customerId = route.screen === "customer" ? route.id ?? null : null;

  useEffect(() => {
    const onHash = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const go = useCallback((s: Screen, id?: string) => {
    navigate({ screen: s, id });
    setRoute({ screen: s, id });
  }, []);

  const flash = (msg: string, undo?: () => void) => {
    setToast({ msg, undo });
    setTimeout(() => setToast(null), undo ? 9000 : 3500);
  };

  const signOut = useCallback(() => {
    clearPlatformSession();
    setSession(null);
    setSummaries(null);
    setDetails({});
    setError(null);
  }, []);

  /** A dead session must return the user to sign-in, not strand them inside
   *  application chrome that looks live but can load nothing. */
  const handleAuthLoss = useCallback(() => {
    signOut();
    // The toast lives inside the signed-in shell, which is about to unmount —
    // the message has to survive onto the sign-in screen to be seen at all.
    setNotice("Your session expired. Please sign in again.");
  }, [signOut]);

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
      if (isAuthError(e)) return handleAuthLoss();
      // Keep whatever we last knew, but never let a stale/empty list be
      // presented as "nothing to do" — see the error branch in the render.
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [session, handleAuthLoss]);

  useEffect(() => {
    if (session) load();
  }, [session, load]);

  const signIn = (s: PlatformSession) => {
    setNotice(null);
    savePlatformSession(s);
    setSession(s);
    go("home");
  };
  const switchRole = async (email: string) => {
    try {
      const r = await papi.login(email, "demo");
      signIn({ token: r.token, role: r.role, name: r.name, user_id: r.user_id, organization_id: r.organization_id });
    } catch (e) {
      flash((e as Error).message);
    }
  };

  const openDetail = (id: string) => go("detail", id);

  const refresh = useCallback(async (id?: string) => {
    if (!session) return;
    if (id) {
      const d = await papi.getDetail(session.token, id);
      setDetails((m) => ({ ...m, [id]: d }));
    }
    setSummaries(await papi.listDecisions(session.token));
  }, [session]);

  const undoAction = async (id: string) => {
    if (!session) return;
    try {
      await papi.reopen(session.token, id);
      await refresh(id);
      flash("Restored to your queue.");
    } catch (e) {
      if (isAuthError(e)) return handleAuthLoss();
      flash((e as Error).message);
    }
  };

  const doAction = async (id: string, kind: string, note: string) => {
    if (!session) return;
    setBusy(true);
    try {
      const meta = ACTION_META[kind];
      await papi.act(session.token, id, { action: meta.api, note, reason: kind === "dismiss" ? note : undefined });
      setModal(null);
      await refresh(id);
      // Every action here closes or changes a decision. Offer the way back:
      // one misclick should never be unrecoverable.
      flash(`${meta.title} · logged`, () => undoAction(id));
    } catch (e) {
      if (isAuthError(e)) return handleAuthLoss();
      flash((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const openDecisions = useMemo(
    () => (summaries || []).filter((s) => s.status === "OPEN" || s.status === "VIEWED"),
    [summaries],
  );

  if (!session) return <SignIn onIn={signIn} notice={notice} />;

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
              onClick={() => go(key)}
            >
              {label}
              {count && <span className="n-count">{count}</span>}
            </button>
          ))}
        </div>
        <span className="dp-spacer" />
        {/* Demo affordance only. In a real deployment a user has one role and a
            role switcher in the product chrome would be confusing at best. */}
        {import.meta.env.DEV && (
          <div className="dp-role" role="group" aria-label="Switch role (demo only)">
            {DEMO_ACCOUNTS.map((a) => (
              <button key={a.role} className={session.role === a.role ? "on" : ""} onClick={() => switchRole(a.email)}>
                {a.label}
              </button>
            ))}
          </div>
        )}
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
        {/* An unreachable API must never be dressed as "nothing to do". The
            error REPLACES the queue rather than sitting above a reassuring
            empty state — the previous behaviour told a salesperson everything
            was fine at exactly the moment the system knew nothing. */}
        {error ? (
          <LoadFailed error={error} onRetry={load} busy={loading} />
        ) : (
          <>
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
            onBack={() => go("list")}
            onAct={(kind) => setModal({ id: detailId, kind })}
            onOpenAccount={(cid) => go("customer", cid)}
          />
        )}

        {/* ── ACCOUNTS (customer intelligence) ── */}
        {screen === "customer" && (
          <CustomerScreen
            session={session}
            details={details}
            customerId={customerId}
            setCustomerId={(id) => go("customer", id ?? undefined)}
            onOpen={openDetail}
          />
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
          </>
        )}
      </div>

      {modal && (
        <ActionModal
          kind={modal.kind}
          busy={busy}
          onClose={() => setModal(null)}
          onConfirm={(note) => doAction(modal.id, modal.kind, note)}
        />
      )}
      {toast && (
        <div className="toast">
          <span>{toast.msg}</span>
          {toast.undo && (
            <button
              className="toast-undo"
              onClick={() => {
                const u = toast.undo!;
                setToast(null);
                u();
              }}
            >
              Undo
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// ── load failure ─────────────────────────────────────────────────────────────
/** Shown INSTEAD OF the queue when decisions could not be loaded.
 *
 * The distinction that matters: "we could not look" is not "there is nothing
 * to see". This screen never implies the latter, and it always offers the way
 * back rather than requiring a manual page reload. */
function LoadFailed({ error, onRetry, busy }: { error: string; onRetry: () => void; busy: boolean }) {
  return (
    <div className="dp-head" style={{ maxWidth: 620 }}>
      <h1>Decisions could not be loaded</h1>
      <p>
        This is a loading failure, not an empty queue — there may well be decisions waiting. Nothing
        has been lost; your data is untouched.
      </p>
      <div className="state-panel" style={{ marginTop: 14 }}>
        <div className="state-mark">What went wrong</div>
        <p style={{ margin: 0, fontSize: 13.5 }}>{error}</p>
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
        <button className="btn btn-primary" onClick={onRetry} disabled={busy}>
          {busy ? "Retrying…" : "Try again"}
        </button>
      </div>
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
            <Conf level={d.confidence?.evidence_sufficiency} aiStatus={d.interpretation.status} />
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
  session,
  details,
  customerId,
  setCustomerId,
  onOpen,
}: {
  session: PlatformSession;
  details: Record<string, DecisionDetail>;
  customerId: string | null;
  setCustomerId: (id: string | null) => void;
  onOpen: (id: string) => void;
}) {
  const all = Object.values(details);
  // The directory is every account in scope — not only those that happen to
  // have an open decision. "What does this account look like before I call
  // them?" is the question this screen exists to answer, and it cannot be
  // answered for a quiet customer if quiet customers are invisible.
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [q, setQ] = useState("");
  const [accErr, setAccErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    papi
      .listAccounts(session.token)
      .then((a) => !cancelled && setAccounts(a))
      .catch((e) => !cancelled && setAccErr((e as Error).message));
    return () => {
      cancelled = true;
    };
  }, [session.token]);

  const openCountFor = (id: string) =>
    all.filter((d) => d.subject_entity_id === id && (d.status === "OPEN" || d.status === "VIEWED")).length;

  if (!customerId) {
    const needle = q.trim().toLowerCase();
    const shown = (accounts || []).filter((a) => !needle || a.name.toLowerCase().includes(needle));
    return (
      <div>
        <div className="dp-head">
          <h1>Accounts</h1>
          <p>Every account you cover — search one to see what the data says before you call.</p>
        </div>
        <input
          className="input"
          style={{ maxWidth: 320, marginBottom: 14 }}
          placeholder="Search accounts…"
          aria-label="Search accounts"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        {accErr ? (
          <div className="state-panel">
            <div className="state-mark">Accounts could not be loaded</div>
            <p style={{ margin: 0, fontSize: 13.5 }}>{accErr}</p>
          </div>
        ) : accounts === null ? (
          <>
            <div className="skeleton" style={{ height: 44 }} />
            <div className="skeleton" style={{ height: 44 }} />
          </>
        ) : shown.length === 0 ? (
          <div className="dp-empty">
            {needle ? `No account matches “${q}”.` : "No accounts are assigned to you yet."}
          </div>
        ) : (
          <Bp style={{ padding: 2 }}>
            <table className="dp-table">
              <thead>
                <tr>
                  <th>Account</th>
                  <th>Open decisions</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((a) => {
                  const n = openCountFor(a.customer_id);
                  return (
                    <tr key={a.customer_id} data-open onClick={() => setCustomerId(a.customer_id)}>
                      <td style={{ fontWeight: 600 }}>{a.name}</td>
                      <td>{n > 0 ? `${n} open` : <span className="text-muted">none</span>}</td>
                      <td style={{ fontSize: 12 }}>{a.status}</td>
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
  const decs = all.filter((d) => d.subject_entity_id === customerId);
  const name = decs[0]?.subject_label || accounts?.find((a) => a.customer_id === customerId)?.name || customerId;
  return (
    <div>
      <button className="btn btn-ghost btn-sm" onClick={() => setCustomerId(null)} style={{ marginBottom: 10 }}>
        ← All accounts
      </button>
      <div className="dp-head">
        <h1>{name}</h1>
        <p>Trading facts and what we read from them.</p>
      </div>
      {decs.length === 0 ? (
        <div className="dp-empty">
          Nothing is flagged on this account right now. That is a fact about the data, not a
          judgement about the relationship.
        </div>
      ) : (
      <>
      <div className="dp-count">
        {decs.length} open {decs.length === 1 ? "decision" : "decisions"} on this account
        {decs.length > 1 && " — the same account is flagged for more than one reason"}
      </div>
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
      </>
      )}
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
