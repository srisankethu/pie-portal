import { useCallback, useEffect, useState } from "react";
import { papi } from "./api";
import type {
  ApprovalRequest,
  OrgPolicy,
  PlatformSession,
  PlatformUser,
  Role,
  ThresholdView,
} from "./types";
import { Bp } from "./ui";

/**
 * The approval queue and organization settings.
 *
 * Both screens exist because the platform previously computed authority without
 * enforcing it: a price under the floor was flagged and then sent, and
 * "escalate to management" closed the decision without telling management
 * anything. The queue is where that now lands; settings is where an owner
 * decides how strict it should be and who is allowed to answer it.
 */

const ROLE_LABEL: Record<Role, string> = {
  SALESPERSON: "Salesperson",
  SALES_MANAGER: "Sales manager",
  OWNER: "Owner",
};

const ROLE_HELP: Record<Role, string> = {
  SALESPERSON: "Their own accounts. Never sees cost or margin.",
  SALES_MANAGER: "The whole organization, with full economics. Approves thin prices.",
  OWNER: "Everything, plus users, roles and policy. Approves selling below cost.",
};

const KIND_LABEL: Record<string, string> = {
  QUOTE_LINE_PRICE: "Quote price",
  QUOTE_SUBMISSION: "Quote submission",
  DECISION_ESCALATION: "Escalated decision",
};

function inr(n: unknown): string {
  return typeof n === "number" ? "₹" + Math.round(n).toLocaleString("en-IN") : "—";
}

function pct(n: unknown): string {
  return typeof n === "number" ? (n * 100).toFixed(1) + "%" : "—";
}

function when(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-IN", {
    day: "numeric", month: "short", hour: "numeric", minute: "2-digit",
  });
}

/* ── approvals ────────────────────────────────────────────────────────────── */

function ApprovalCard({
  req,
  session,
  onDecide,
}: {
  req: ApprovalRequest;
  session: PlatformSession;
  onDecide: (id: string, status: string, note: string) => Promise<void>;
}) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const s = (req.subject ?? {}) as Record<string, number | string | null>;
  const isMine = req.requested_by_user_id === session.user_id;

  async function act(status: string) {
    setBusy(true);
    try {
      await onDecide(req.approval_request_id, status, note);
      setNote("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Bp className={`ap-card ap-${req.status.toLowerCase()}`}>
      <div className="ap-head">
        <div>
          <span className="ap-kind">{KIND_LABEL[req.kind] ?? req.kind}</span>
          {req.required_authority === "OWNER" && <span className="ap-owner">Owner only</span>}
          <h4>{req.title}</h4>
          <div className="ap-summary">{req.summary}</div>
        </div>
        <span className={`ap-status ${req.status.toLowerCase()}`}>{req.status.replace("_", " ")}</span>
      </div>

      <div className="ap-meta">
        Raised by <b>{req.requested_by}</b> · {when(req.requested_at)}
        {req.decided_by && (
          <> · {req.status.toLowerCase().replace("_", " ")} by <b>{req.decided_by}</b> {when(req.decided_at)}</>
        )}
      </div>

      {req.reason && <div className="ap-reason">“{req.reason}”</div>}

      {/* Economics reach an approver and nobody else — the server omits the
          whole subject for a salesperson, including on their own request. */}
      {req.subject && req.kind === "QUOTE_LINE_PRICE" && (
        <dl className="ap-econ">
          <div><dt>Quoted</dt><dd>{inr(s.quoted_unit_price)}</dd></div>
          <div><dt>Effective cost</dt><dd>{inr(s.unit_cost)}</dd></div>
          <div><dt>Margin</dt><dd className={s.below_cost ? "warn" : ""}>{pct(s.margin)}</dd></div>
          <div><dt>Line value</dt><dd>{inr(s.line_revenue)}</dd></div>
          <div><dt>Quantity</dt><dd>{String(s.qty ?? "—")} · band {String(s.quantity_band ?? "—")}</dd></div>
          <div><dt>Gross profit</dt><dd>{inr(s.gross_profit)}</dd></div>
        </dl>
      )}

      {req.decision_note && <div className="ap-decision-note">{req.decision_note}</div>}

      <button className="btn btn-ghost btn-sm" onClick={() => setOpen((o) => !o)}>
        {open ? "Hide history" : `History (${req.thread.length})`}
      </button>
      {open && (
        <ol className="ap-thread">
          {req.thread.map((t, i) => (
            <li key={i}>
              <b>{t.name}</b> {t.action.toLowerCase().replace("_", " ")} · {when(t.at)}
              {t.note && <div className="ap-thread-note">{t.note}</div>}
            </li>
          ))}
        </ol>
      )}

      {req.is_open && req.can_decide && (
        <div className="ap-actions">
          <input
            className="input"
            placeholder="Add a note (required to reject or return)"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            aria-label="Decision note"
          />
          <div className="ap-buttons">
            <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => act("APPROVED")}>
              Approve
            </button>
            <button
              className="btn btn-secondary btn-sm"
              disabled={busy || !note.trim()}
              onClick={() => act("CHANGES_REQUESTED")}
            >
              Ask for a different price
            </button>
            <button
              className="btn btn-ghost btn-sm"
              disabled={busy || !note.trim()}
              onClick={() => act("REJECTED")}
            >
              Reject
            </button>
          </div>
        </div>
      )}

      {req.is_open && !req.can_decide && (
        <div className="ap-waiting">
          {isMine
            ? `Waiting on ${req.required_authority === "OWNER" ? "an owner" : "a manager"}.`
            : "You are not authorized to decide this one."}
          {isMine && (
            <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => act("WITHDRAWN")}>
              Withdraw
            </button>
          )}
        </div>
      )}
    </Bp>
  );
}

export function ApprovalsScreen({ session }: { session: PlatformSession }) {
  const [requests, setRequests] = useState<ApprovalRequest[]>([]);
  const [pending, setPending] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showClosed, setShowClosed] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await papi.listApprovals(session.token);
      setRequests(r.requests);
      setPending(r.pending_for_me);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [session.token]);

  useEffect(() => {
    load();
  }, [load]);

  async function decide(id: string, status: string, note: string) {
    try {
      await papi.decideApproval(session.token, id, status, note || undefined);
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const visible = requests.filter((r) => showClosed || r.is_open);

  return (
    <div className="dp-screen">
      <div className="dp-screen-head">
        <div>
          <h2>Approvals</h2>
          <p className="text-muted">
            {session.role === "SALESPERSON"
              ? "Prices you have asked someone to sign off, and what they said."
              : "Prices and decisions waiting on your judgement. A quote with an unapproved line cannot be sent."}
          </p>
        </div>
        <div className="dp-screen-actions">
          {pending > 0 && <span className="ap-pill">{pending} waiting on you</span>}
          <label className="ap-toggle">
            <input type="checkbox" checked={showClosed} onChange={(e) => setShowClosed(e.target.checked)} />
            Show settled
          </label>
        </div>
      </div>

      {error && <div className="dp-error">{error}</div>}
      {loading && <div className="text-muted">Loading…</div>}

      {!loading && visible.length === 0 && (
        <Bp className="dp-empty">
          <h4>Nothing waiting.</h4>
          <p>
            {session.role === "SALESPERSON"
              ? "When a price needs sign-off, ask for it from the quote line and it will appear here."
              : "Requests appear here the moment someone asks for a price you have to judge."}
          </p>
        </Bp>
      )}

      <div className="ap-list">
        {visible.map((r) => (
          <ApprovalCard key={r.approval_request_id} req={r} session={session} onDecide={decide} />
        ))}
      </div>
    </div>
  );
}

/* ── settings ─────────────────────────────────────────────────────────────── */

function NewUserForm({
  token,
  onCreated,
}: {
  token: string;
  onCreated: (password: string, email: string) => void;
}) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<Role>("SALESPERSON");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await papi.createUser(token, { email, name, role });
      onCreated(r.temporary_password, r.user.email ?? email);
      setEmail("");
      setName("");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="st-newuser" onSubmit={submit}>
      <input className="input" placeholder="name@company.com" value={email}
             onChange={(e) => setEmail(e.target.value)} required aria-label="Email" />
      <input className="input" placeholder="Full name" value={name}
             onChange={(e) => setName(e.target.value)} required aria-label="Name" />
      <select className="input" value={role} onChange={(e) => setRole(e.target.value as Role)}
              aria-label="Role">
        {(Object.keys(ROLE_LABEL) as Role[]).map((r) => (
          <option key={r} value={r}>{ROLE_LABEL[r]}</option>
        ))}
      </select>
      <button className="btn btn-primary" disabled={busy}>{busy ? "Creating…" : "Add user"}</button>
      {error && <div className="dp-error st-span">{error}</div>}
      <div className="st-help st-span">{ROLE_HELP[role]}</div>
    </form>
  );
}

export function SettingsScreen({ session }: { session: PlatformSession }) {
  const [users, setUsers] = useState<PlatformUser[]>([]);
  const [canManage, setCanManage] = useState(false);
  const [policy, setPolicy] = useState<OrgPolicy | null>(null);
  const [thresholds, setThresholds] = useState<ThresholdView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [issued, setIssued] = useState<{ email: string; password: string } | null>(null);
  const [pw, setPw] = useState({ current: "", next: "" });
  const [pwMsg, setPwMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [u, p] = await Promise.all([
        papi.listUsers(session.token),
        papi.getPolicy(session.token),
      ]);
      setUsers(u.users);
      setCanManage(u.can_manage);
      setPolicy(p.policy);
      setThresholds(p.thresholds);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [session.token]);

  useEffect(() => {
    load();
  }, [load]);

  async function patchUser(id: string, body: { role?: Role; active?: boolean }) {
    try {
      await papi.updateUser(session.token, id, body);
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function reset(id: string, email: string | null) {
    try {
      const r = await papi.resetUserPassword(session.token, id);
      setIssued({ email: email ?? id, password: r.temporary_password });
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function togglePolicy(key: keyof OrgPolicy, value: boolean) {
    try {
      const next = await papi.updatePolicy(session.token, { [key]: value });
      setPolicy(next);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function changePassword(e: React.FormEvent) {
    e.preventDefault();
    setPwMsg(null);
    try {
      await papi.changeOwnPassword(session.token, pw.current, pw.next);
      setPwMsg("Password changed.");
      setPw({ current: "", next: "" });
    } catch (err) {
      setPwMsg((err as Error).message);
    }
  }

  const isSales = session.role === "SALESPERSON";

  return (
    <div className="dp-screen">
      <div className="dp-screen-head">
        <div>
          <h2>Settings</h2>
          <p className="text-muted">
            {canManage
              ? "You are the owner: accounts, roles and approval policy are yours."
              : "Your account, and how this organization is configured."}
          </p>
        </div>
      </div>

      {error && <div className="dp-error">{error}</div>}

      {/* ── your own account ── */}
      <Bp className="st-section">
        <h3>Your account</h3>
        <div className="st-me">
          <div><span className="st-label">Name</span><b>{session.name}</b></div>
          <div><span className="st-label">Role</span><b>{ROLE_LABEL[session.role]}</b></div>
        </div>
        <p className="st-help">
          {ROLE_HELP[session.role]} Your role comes from your account and cannot be
          changed from this screen — an owner sets it.
        </p>
        <form className="st-pw" onSubmit={changePassword}>
          <input className="input" type="password" placeholder="Current password"
                 autoComplete="current-password" value={pw.current}
                 onChange={(e) => setPw({ ...pw, current: e.target.value })}
                 required aria-label="Current password" />
          <input className="input" type="password" placeholder="New password (10+ characters)"
                 autoComplete="new-password" value={pw.next}
                 onChange={(e) => setPw({ ...pw, next: e.target.value })}
                 required aria-label="New password" />
          <button className="btn btn-secondary">Change password</button>
          {pwMsg && <div className="st-span st-help">{pwMsg}</div>}
        </form>
      </Bp>

      {/* ── users and roles ── */}
      {!isSales && (
        <Bp className="st-section">
          <h3>People and roles</h3>
          <p className="st-help">
            A role is a boundary, not a view preference: a salesperson never receives
            cost or margin from the API, whatever screen they are on.
          </p>

          {canManage && <NewUserForm token={session.token} onCreated={(password, email) => {
            setIssued({ email, password });
            load();
          }} />}

          {issued && (
            <div className="st-issued">
              Temporary password for <b>{issued.email}</b>: <code>{issued.password}</code>
              <div className="st-help">
                Shown once — it is stored only as a hash. They are asked to change it at
                first sign-in.
              </div>
              <button className="btn btn-ghost btn-sm" onClick={() => setIssued(null)}>Dismiss</button>
            </div>
          )}

          <table className="st-table">
            <thead>
              <tr>
                <th>Name</th><th>Email</th><th>Role</th><th>Status</th><th>Last sign-in</th>
                {canManage && <th></th>}
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.user_id} className={u.active ? "" : "st-inactive"}>
                  <td>
                    {u.name}
                    {u.user_id === session.user_id && <span className="st-you">you</span>}
                  </td>
                  <td className="mono">{u.email}</td>
                  <td>
                    {canManage && u.user_id !== session.user_id ? (
                      <select
                        className="input st-role"
                        value={u.role}
                        onChange={(e) => patchUser(u.user_id, { role: e.target.value as Role })}
                        aria-label={`Role for ${u.name}`}
                      >
                        {(Object.keys(ROLE_LABEL) as Role[]).map((r) => (
                          <option key={r} value={r}>{ROLE_LABEL[r]}</option>
                        ))}
                      </select>
                    ) : (
                      ROLE_LABEL[u.role]
                    )}
                    {u.role_changed_by && (
                      <div className="st-help">changed by {u.role_changed_by}</div>
                    )}
                  </td>
                  <td>
                    {!u.active ? <span className="st-badge off">deactivated</span>
                      : !u.has_password ? <span className="st-badge warn">no password</span>
                      : u.must_change_password ? <span className="st-badge warn">must change</span>
                      : <span className="st-badge ok">active</span>}
                  </td>
                  <td className="text-muted">{when(u.last_login_at)}</td>
                  {canManage && (
                    <td className="st-rowactions">
                      {u.user_id !== session.user_id && (
                        <>
                          <button className="btn btn-ghost btn-sm"
                                  onClick={() => reset(u.user_id, u.email)}>
                            Reset password
                          </button>
                          <button className="btn btn-ghost btn-sm"
                                  onClick={() => patchUser(u.user_id, { active: !u.active })}>
                            {u.active ? "Deactivate" : "Reactivate"}
                          </button>
                        </>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </Bp>
      )}

      {/* ── approval policy ── */}
      {!isSales && policy && (
        <Bp className="st-section">
          <h3>Approval policy</h3>
          <p className="st-help">
            What the platform refuses, and whose signature lifts it. Owner only —
            a manager who could widen their own authority would not have any.
          </p>
          {([
            ["require_approval_for_quotes",
             "Block quotes with unapproved lines",
             "Off, the platform advises and records but refuses nothing."],
            ["below_cost_requires_owner",
             "Selling below cost needs an owner",
             "A thin margin is a manager's call; losing money on purpose is not."],
            ["require_approval_below_review_floor",
             "Also require approval below the review floor",
             "Stricter. Asking for sign-off on every thin line trains people to rubber stamp."],
            ["allow_self_approval",
             "Allow managers to approve their own requests",
             "Owners always can — in a small business they are often the only approver."],
            ["escalation_creates_approval",
             "Escalating a decision raises a request",
             "Off, escalation only marks the decision and nobody is told."],
          ] as [keyof OrgPolicy, string, string][]).map(([key, label, help]) => (
            <label key={key} className={`st-switch ${canManage ? "" : "readonly"}`}>
              <input
                type="checkbox"
                checked={Boolean(policy[key])}
                disabled={!canManage}
                onChange={(e) => togglePolicy(key, e.target.checked)}
              />
              <span>
                <b>{label}</b>
                <span className="st-help">{help}</span>
              </span>
            </label>
          ))}
        </Bp>
      )}

      {/* ── margin policy (read-only) ── */}
      {!isSales && thresholds && (
        <Bp className="st-section">
          <h3>Margin policy</h3>
          <p className="st-help">
            What trips an approval in the first place. Read-only here and set by
            environment configuration: a threshold quietly editable from a settings
            screen is a threshold nobody can reproduce a past number against. Version{" "}
            <code>{thresholds.version}</code>.
          </p>
          <dl className="st-thresholds">
            <div><dt>Target margin</dt><dd>{pct(thresholds.target_margin_default)}</dd></div>
            <div><dt>Review floor</dt><dd>{pct(thresholds.margin_floor)}</dd></div>
            <div><dt>Approval floor</dt><dd>{pct(thresholds.min_margin)}</dd></div>
            <div><dt>Sales discretion</dt><dd>±{pct(thresholds.sales_discretion_band)}</dd></div>
            <div><dt>Quantity bands</dt><dd>{thresholds.quantity_band_edges.join(" · ")}</dd></div>
            <div><dt>Exception floor</dt><dd>{inr(thresholds.min_quote_exception_impact_rupees)}</dd></div>
          </dl>
          <div className="st-families">
            {Object.entries(thresholds.target_margin_by_family).map(([f, m]) => (
              <span key={f} className="st-family">{f.replace(/_/g, " ")} {pct(m)}</span>
            ))}
          </div>
        </Bp>
      )}
    </div>
  );
}
