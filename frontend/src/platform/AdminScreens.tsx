import { useCallback, useEffect, useMemo, useState } from "react";
import { formatDateTime } from "../when";
import { papi } from "./api";
import type {
  AiMetricsReport,
  AiReadiness,
  ApprovalRequest,
  FixedThresholds,
  MarginPolicy,
  MarginPolicyPatch,
  OrgPolicy,
  PlatformSession,
  PlatformUser,
  PolicyField,
  Role } from "./types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { abilityFor } from "./ability";
import { DataGrid, type ColDef } from "./DataGrid";
import { EmptyState, ErrorState, LoadingState, MetricCard, StatusChip, type Tone } from "./kit";
import { policyProblems } from "./policySchema";
import { Bp, Labelled } from "./ui";
import { money, moneySymbol } from "../money";

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
  OWNER: "Owner" };

const ROLE_HELP: Record<Role, string> = {
  SALESPERSON: "Their own accounts. Never sees cost or margin.",
  SALES_MANAGER: "The whole organization, with full economics. Approves thin prices.",
  OWNER: "Everything, plus users, roles and policy. Approves selling below cost." };

const KIND_LABEL: Record<string, string> = {
  QUOTE_LINE_PRICE: "Quote price",
  QUOTE_SUBMISSION: "Quote submission",
  DECISION_ESCALATION: "Escalated decision" };


function pct(n: unknown): string {
  return typeof n === "number" ? (n * 100).toFixed(1) + "%" : "—";
}

const when = formatDateTime;

/* ── approvals ────────────────────────────────────────────────────────────── */

function ApprovalCard({
  req,
  session,
  onDecide }: {
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
          <div><dt>Quoted</dt><dd>{money(s.quoted_unit_price)}</dd></div>
          <div>
            <dt>
              <Labelled tip="Purchase cost after landed costs and supplier discounts — what this piece actually cost to put on the shelf, not the list price of the item.">
                Effective cost
              </Labelled>
            </dt>
            <dd>{money(s.unit_cost)}</dd>
          </div>
          <div>
            <dt>
              <Labelled tip="Gross profit ÷ revenue on this line. Below the approval floor it needs a signature; below zero the price is under cost.">
                Margin
              </Labelled>
            </dt>
            <dd className={s.below_cost ? "warn" : ""}>{pct(s.margin)}</dd>
          </div>
          <div><dt>Line value</dt><dd>{money(s.line_revenue)}</dd></div>
          <div>
            <dt>
              <Labelled tip="Which quantity bracket this falls in. Prices are compared within a band — the same item at 5 pieces and 500 is a different commercial question.">
                Quantity
              </Labelled>
            </dt>
            <dd>{String(s.qty ?? "—")} · band {String(s.quantity_band ?? "—")}</dd>
          </div>
          <div><dt>Gross profit</dt><dd>{money(s.gross_profit)}</dd></div>
        </dl>
      )}

      {req.decision_note && <div className="ap-decision-note">{req.decision_note}</div>}

      <Button variant="text" size="small" onClick={() => setOpen((o) => !o)}>
        {open ? "Hide history" : `History (${req.thread.length})`}
      </Button>
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
            <Button variant="contained" size="small" disabled={busy} onClick={() => act("APPROVED")}>
              Approve
            </Button>
            <Button
              variant="outlined" size="small"
              disabled={busy || !note.trim()}
              onClick={() => act("CHANGES_REQUESTED")}
            >
              Ask for a different price
            </Button>
            <Button
              variant="text" size="small"
              disabled={busy || !note.trim()}
              onClick={() => act("REJECTED")}
            >
              Reject
            </Button>
          </div>
        </div>
      )}

      {req.is_open && !req.can_decide && (
        <div className="ap-waiting">
          {isMine
            ? `Waiting on ${req.required_authority === "OWNER" ? "an owner" : "a manager"}.`
            : "You are not authorized to decide this one."}
          {isMine && (
            <Button variant="text" size="small" disabled={busy} onClick={() => act("WITHDRAWN")}>
              Withdraw
            </Button>
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
          <h2>
            <Labelled
              tip={
                <>
                  A request is raised when a price crosses a policy boundary — under the
                  approval floor, or under cost. It is not advisory: a quote with an
                  unapproved line is refused at the point it would be sent, and an approval
                  covers the price it was granted at, so re-pricing lower needs asking again.
                </>
              }
            >
              Approvals
            </Labelled>
          </h2>
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

/* ── margin policy ────────────────────────────────────────────────────────── */

/**
 * The numbers that decide what a quote is judged against, editable by an owner.
 *
 * Three things this screen has to get right, all of which are ways a margin
 * policy goes wrong quietly:
 *
 * **Units.** A margin is stored as a ratio and read as a percentage. The field
 * shows 24 and sends 0.24, because typing 24 into a field that means 0.24 sets
 * a 2400% target and every quote in the organization is suddenly below floor.
 *
 * **The ladder.** Approval floor ≤ review floor ≤ target. Inverted, a line is
 * flagged for review and cleared for sending at the same time, and the screens
 * argue with each other. It is drawn here in order and refused by the server.
 *
 * **Reproducibility.** Editing changes the threshold version, and figures
 * already computed keep the version they were computed with. That is stated
 * rather than implied — otherwise an owner lowers a floor and reasonably
 * expects yesterday's flags to disappear.
 */

const PP_FIELDS = new Set(["min_margin_deterioration_pp"]);

type Draft = Record<string, string>;
type Families = [string, string][];

function num(v: unknown): number {
  return typeof v === "number" ? v : Number(v);
}

/** A stored value rendered into the box the user types in.
 *
 * Ratios are the only kind that scale — a ratio is stored as 0.24 and typed as
 * 24. A day count that fell through to this branch was shown as "36500" for a
 * 365-day threshold, so the kinds are exhaustive here rather than defaulting. */
function toInput(f: PolicyField): string {
  if (f.kind === "ratio") return String(Math.round(num(f.value) * 10000) / 100);
  if (f.kind === "money") return String(num(f.value));
  if (f.kind === "days") return String(Math.round(num(f.value)));
  if (f.kind === "flag") return f.value ? "true" : "false";
  if (f.kind === "band_edges") return (f.value as number[]).join(", ");
  return "";
}

function toFamilies(f: PolicyField | undefined): Families {
  if (!f) return [];
  return Object.entries((f.value as Record<string, number>) || {}).map(
    ([k, v]) => [k, String(Math.round(v * 10000) / 100)],
  );
}

function unitFor(f: PolicyField): string {
  // The symbol follows the organization's currency; it was "₹" regardless.
  if (f.kind === "money") return moneySymbol();
  if (f.kind === "ratio") return PP_FIELDS.has(f.field) ? "pp" : "%";
  if (f.kind === "days") return "days";
  return "";
}

/** "reset to 24%", "reset to ₹500", "reset to 365 days", "reset to off". */
function resetLabel(f: PolicyField): string {
  if (f.kind === "flag") return f.default ? "on" : "off";
  const value = toInput({ ...f, value: f.default });
  const unit = unitFor(f);
  if (f.kind === "money") return `${unit}${value}`;
  if (f.kind === "days") return `${value} ${unit}`;
  return `${value}${unit}`;
}

function MarginPolicySection({
  token,
  policy,
  fixed,
  canManage,
  onSaved }: {
  token: string;
  policy: MarginPolicy;
  fixed: FixedThresholds | null;
  canManage: boolean;
  onSaved: (p: MarginPolicy, note: string) => void;
}) {
  const byName = useMemo(() => {
    const m: Record<string, PolicyField> = {};
    policy.fields.forEach((f) => (m[f.field] = f));
    return m;
  }, [policy]);

  const [draft, setDraft] = useState<Draft>({});
  const [families, setFamilies] = useState<Families>([]);
  const [cleared, setCleared] = useState<string[]>([]);
  const [msg, setMsg] = useState<{ text: string; bad: boolean } | null>(null);
  const [busy, setBusy] = useState(false);

  // Re-seed whenever the server's version of the policy changes, so a save (or
  // a reset) leaves the boxes showing what is actually in force.
  const reseed = useCallback(() => {
    const d: Draft = {};
    policy.fields.forEach((f) => {
      if (f.kind !== "family_margins") d[f.field] = toInput(f);
    });
    setDraft(d);
    setFamilies(toFamilies(byName.target_margin_by_family));
    setCleared([]);
  }, [policy, byName]);

  useEffect(reseed, [reseed]);

  const scalars = policy.fields.filter((f) => f.kind !== "family_margins");

  /** What the boxes currently say, as ratios — used for the live ladder. */
  const live = useMemo(() => {
    const out: Record<string, number> = {};
    scalars.forEach((f) => {
      const raw = Number(draft[f.field]);
      if (!Number.isFinite(raw)) return;
      out[f.field] = f.kind === "ratio" ? raw / 100 : raw;
    });
    return out;
  }, [draft, scalars]);

  // Every rule the draft breaks, keyed by field, recomputed as it is typed.
  // The form used to validate nothing until Save and then throw on the first
  // bad field, so somebody who mistyped three boxes learned about them one
  // round trip at a time.
  const problems = useMemo(
    () => policyProblems(scalars, draft), [scalars, draft]);
  const ladderOk = !problems.min_margin && !problems.margin_floor;

  const dirty =
    cleared.length > 0 ||
    scalars.some((f) => draft[f.field] !== undefined && draft[f.field] !== toInput(f)) ||
    JSON.stringify(families) !== JSON.stringify(toFamilies(byName.target_margin_by_family));

  function reset(field: string) {
    const f = byName[field];
    if (!f) return;
    setCleared((c) => (c.includes(field) ? c : [...c, field]));
    if (f.kind === "family_margins") {
      setFamilies(
        Object.entries((f.default as Record<string, number>) || {}).map(
          ([k, v]) => [k, String(Math.round(v * 10000) / 100)],
        ),
      );
    } else {
      setDraft((d) => ({ ...d, [field]: toInput({ ...f, value: f.default }) }));
    }
  }

  async function save() {
    setBusy(true);
    setMsg(null);
    try {
      const patch: MarginPolicyPatch = {};
      const clear = [...cleared];

      for (const f of scalars) {
        if (clear.includes(f.field)) continue;
        const raw = draft[f.field];
        if (raw === undefined || raw === toInput(f)) continue;
        if (f.kind === "band_edges") {
          const edges = raw
            .split(/[,\s]+/)
            .filter(Boolean)
            .map((s) => Number(s));
          if (edges.some((n) => !Number.isFinite(n))) {
            throw new Error(`${f.label}: only whole numbers, separated by commas.`);
          }
          patch.quantity_band_edges = edges.map((n) => Math.round(n));
        } else if (f.kind === "flag") {
          (patch as Record<string, unknown>)[f.field] = raw === "true";
        } else {
          const n = Number(raw);
          if (!Number.isFinite(n)) throw new Error(`${f.label}: not a number.`);
          if (f.kind === "days" && !(n >= 1)) {
            throw new Error(`${f.label}: must be at least one day.`);
          }
          (patch as Record<string, unknown>)[f.field] =
            f.kind === "ratio" ? n / 100
              : f.kind === "days" ? Math.round(n)
              : n;
        }
      }

      const famField = byName.target_margin_by_family;
      if (
        famField &&
        !clear.includes("target_margin_by_family") &&
        JSON.stringify(families) !== JSON.stringify(toFamilies(famField))
      ) {
        const out: Record<string, number> = {};
        for (const [name, pctText] of families) {
          const key = name.trim();
          if (!key) continue;
          const n = Number(pctText);
          if (!Number.isFinite(n)) throw new Error(`Target margin for ${key}: not a number.`);
          out[key] = n / 100;
        }
        patch.target_margin_by_family = out;
      }

      if (clear.length) patch.clear = clear;
      if (Object.keys(patch).length === 0) {
        setMsg({ text: "Nothing changed.", bad: false });
        return;
      }

      const r = await papi.updateMarginPolicy(token, patch);
      onSaved(r.margin_policy, r.note);
      setCleared([]);
      setMsg({ text: r.note, bad: false });
    } catch (e) {
      setMsg({ text: (e as Error).message, bad: true });
    } finally {
      setBusy(false);
    }
  }

  const changedVersion = policy.version !== policy.default_version;

  return (
    <Bp className="st-section">
      <h3>
        <Labelled
          tip={
            <>
              These decide what a quote is compared against and what trips an approval.
              Editing them changes the threshold <b>version</b>; figures already computed
              keep the version they were computed with until the next recompute, so old
              flags stay explainable.
            </>
          }
        >
          Margin policy
        </Labelled>
      </h3>
      <p className="st-help">
        {canManage
          ? "Yours to set. Analysis internals below are not — window lengths and evidence floors are not preferences."
          : "Set by the owner. Shown here so a flagged price can be argued with."}
      </p>

      <div className="mp-version">
        <span>
          Version in force <code>{policy.version}</code>
        </span>
        {changedVersion ? (
          <span>
            differs from the environment default <code>{policy.default_version}</code>
          </span>
        ) : (
          <span>matching the environment default — nothing overridden yet</span>
        )}
        {policy.updated_at && <span>last edited {when(policy.updated_at)}</span>}
      </div>

      <div className="mp-grid">
        {scalars.map((f) => {
          const overridden = cleared.includes(f.field) ? false : f.overridden;
          const unit = unitFor(f);
          // Currency reads as a prefix (₹1,000), a ratio unit as a suffix
          // (24%). That is a property of the *kind*, not of the glyph — this
          // used to compare the unit against a literal "₹", which silently
          // moved the symbol to the wrong side the moment it stopped being one.
          const prefixed = f.kind === "money";
          const set = (value: string) => {
            setDraft((d) => ({ ...d, [f.field]: value }));
            setCleared((c) => c.filter((k) => k !== f.field));
          };
          return (
            <div className="mp-field" key={f.field}>
              <label htmlFor={`mp-${f.field}`}>
                <Labelled tip={f.help}>{f.label}</Labelled>
              </label>
              {/* A boolean is a switch, not a number box. Rendered as one it
                  read "0" beside a percent sign, with no way to turn it on and
                  nothing to say what 0 meant. */}
              {f.kind === "flag" ? (
                <div className="mp-input">
                  <Switch
                    id={`mp-${f.field}`}
                    size="small"
                    checked={draft[f.field] === "true"}
                    disabled={!canManage}
                    onChange={(e) => set(e.target.checked ? "true" : "false")}
                  />
                  <span className="mp-flag-state">
                    {draft[f.field] === "true" ? "on" : "off"}
                  </span>
                </div>
              ) : (
                <div className="mp-input">
                  {prefixed && <span className="unit">{unit}</span>}
                  <input
                    id={`mp-${f.field}`}
                    className="input"
                    type={f.kind === "band_edges" ? "text" : "number"}
                    step={f.kind === "ratio" ? "0.5" : "1"}
                    // A day count has no fractional part and the spinner should
                    // not offer one.
                    min={f.kind === "days" ? 1 : undefined}
                    inputMode={
                      f.kind === "band_edges" ? "text"
                        : f.kind === "days" ? "numeric" : "decimal"
                    }
                    disabled={!canManage}
                    value={draft[f.field] ?? ""}
                    onChange={(e) => set(e.target.value)}
                  />
                  {unit && !prefixed && <span className="unit">{unit}</span>}
                </div>
              )}
              {problems[f.field] && (
                <div className="mp-problem" role="alert">{problems[f.field]}</div>
              )}
              <div className="mp-state">
                {overridden ? (
                  <>
                    <span className="mp-overridden">overridden</span>
                    {canManage && (
                      <button type="button" onClick={() => reset(f.field)}>
                        reset to {resetLabel(f)}
                      </button>
                    )}
                  </>
                ) : (
                  <span className="mp-default">default</span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {byName.target_margin_by_family && (
        <div className="mp-field mp-wide">
          <label>
            <Labelled tip={byName.target_margin_by_family.help}>
              {byName.target_margin_by_family.label}
            </Labelled>
          </label>
          <div className="mp-families">
            {families.map(([name, pctText], i) => (
              <span className="mp-family" key={i}>
                <input
                  className="input"
                  style={{ width: 130 }}
                  value={name}
                  disabled={!canManage}
                  aria-label={`Family ${i + 1} name`}
                  onChange={(e) =>
                    setFamilies((fs) =>
                      fs.map((row, j) => (j === i ? [e.target.value, row[1]] : row)),
                    )
                  }
                />
                <input
                  className="input"
                  value={pctText}
                  disabled={!canManage}
                  aria-label={`Target margin for ${name || `family ${i + 1}`}`}
                  onChange={(e) =>
                    setFamilies((fs) =>
                      fs.map((row, j) => (j === i ? [row[0], e.target.value] : row)),
                    )
                  }
                />
                <span className="unit">%</span>
                {canManage && (
                  <button
                    type="button"
                    aria-label={`Remove ${name || "family"}`}
                    onClick={() => setFamilies((fs) => fs.filter((_, j) => j !== i))}
                  >
                    ×
                  </button>
                )}
              </span>
            ))}
            {canManage && (
              <Button
                type="button"
                variant="text" size="small"
                onClick={() => setFamilies((fs) => [...fs, ["", ""]])}
              >
                Add a family
              </Button>
            )}
            {families.length === 0 && !canManage && (
              <span className="st-help">No family overrides — the default applies to everything.</span>
            )}
          </div>
        </div>
      )}

      <div className={`mp-ladder ${ladderOk ? "" : "bad"}`}>
        {ladderOk ? (
          <>
            Ladder holds: approval floor {pct(live.min_margin)} ≤ review floor{" "}
            {pct(live.margin_floor)} ≤ target {pct(live.target_margin_default)}. Below the
            review floor a line is flagged; below the approval floor it cannot be sent
            without a signature.
          </>
        ) : (
          <>
            These floors are out of order. The approval floor must sit at or below the
            review floor, which must sit at or below the target — otherwise a line is
            flagged for review and cleared for sending at the same time. The server will
            refuse this.
          </>
        )}
      </div>

      {canManage && (
        <div className="mp-bar">
          {/* Any problem blocks, not only the ladder. Letting a save through
              with a bad box meant the server refused it and the message came
              back as a banner detached from the field that caused it. */}
          <Button variant="contained" size="small"
                  disabled={!dirty || busy || Object.keys(problems).length > 0}
                  onClick={save}>
            {busy ? "Saving…" : "Save margin policy"}
          </Button>
          <Button variant="text" size="small" disabled={!dirty || busy} onClick={reseed}>
            Discard changes
          </Button>
          {msg && <span className={`mp-msg ${msg.bad ? "bad" : "ok"}`}>{msg.text}</span>}
        </div>
      )}

      {fixed && (
        <>
          <div className="st-help" style={{ marginTop: 16 }}>
            <Labelled
              tip={
                <>
                  Not preferences. Moving a window length or an evidence floor changes what
                  “eroding” <i>means</i>, so they live in environment configuration where a
                  change is a deployment with a record.
                </>
              }
            >
              <b>Analysis internals</b>
            </Labelled>{" "}
            — fixed, shown for context.
          </div>
          <dl className="mp-fixed">
            <div>
              <dt>
                <Labelled tip="The window treated as 'now' when comparing margin against the period before it.">
                  Recent window
                </Labelled>
              </dt>
              <dd>{fixed.recent_days} days</dd>
            </div>
            <div>
              <dt>Comparison window</dt>
              <dd>{fixed.previous_days} days</dd>
            </div>
            <div>
              <dt>Historical lookback</dt>
              <dd>{fixed.historical_lookback_days} days</dd>
            </div>
            <div>
              <dt>
                <Labelled tip="Fewer transactions than this and the relationship is called INSUFFICIENT — no signal is raised rather than a confident one from thin evidence.">
                  Minimum transactions
                </Labelled>
              </dt>
              <dd>{fixed.min_transactions}</dd>
            </div>
            <div>
              <dt>
                <Labelled tip="A peer benchmark built from fewer customers than this is not published — it would be one customer's price wearing the word 'median'.">
                  Minimum peers
                </Labelled>
              </dt>
              <dd>{fixed.min_peer_customers}</dd>
            </div>
            <div>
              <dt>
                <Labelled tip="The share of a relationship's transactions that must have a known purchase cost before its margin is treated as reliable.">
                  Minimum cost coverage
                </Labelled>
              </dt>
              <dd>{pct(fixed.min_cost_coverage)}</dd>
            </div>
            <div>
              <dt>Peer recency</dt>
              <dd>{fixed.peer_recency_days} days</dd>
            </div>
          </dl>
        </>
      )}
    </Bp>
  );
}

/* ── settings ─────────────────────────────────────────────────────────────── */

function NewUserForm({
  token,
  onCreated }: {
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
      <TextField size="small" type="email" placeholder="name@company.com" value={email}
                 onChange={(e) => setEmail(e.target.value)} required label="Email" />
      <TextField size="small" placeholder="Full name" value={name}
                 onChange={(e) => setName(e.target.value)} required label="Name" />
      <TextField select size="small" value={role} label="Role"
                 onChange={(e) => setRole(e.target.value as Role)}>
        {(Object.keys(ROLE_LABEL) as Role[]).map((r) => (
          <MenuItem key={r} value={r}>{ROLE_LABEL[r]}</MenuItem>
        ))}
      </TextField>
      <Button type="submit" variant="contained" disabled={busy}>
        {busy ? "Creating…" : "Add user"}
      </Button>
      {error && <Alert severity="error" className="st-span" sx={{ mt: 1 }}>{error}</Alert>}
      <div className="st-help st-span">{ROLE_HELP[role]}</div>
    </form>
  );
}

/** The organization's people, as a grid.
 *
 * This was a hand-written `<table class="st-table">`, and it was the last table
 * in the codebase whose row count is set by the size of the business rather than
 * by the shape of the screen — which is the line `platform/DataGrid.tsx` draws.
 * `docs/ui-standards.md` named it in as many words while the quote grid was
 * being converted; this is that entry closed rather than carried.
 *
 * The reason it matters here and not on a fact panel: an owner asking "who can
 * see cost?" wants the managers and owners together, and "who has never signed
 * in?" wants the column sorted. Neither question is answerable by reading a
 * list in insertion order, and a growing team makes that worse every hire.
 *
 * The role cell stays an editable `Select` rather than becoming an ag-grid cell
 * editor. It is not a value being typed — it is an authority being granted, it
 * writes to the server on change, and it is disabled on your own row. An
 * inline editor with a commit-on-Enter contract would make that look like a
 * draft you could abandon.
 */
function UsersGrid({
  users, canManage, selfUserId, onRole, onActive, onReset,
}: {
  users: PlatformUser[];
  canManage: boolean;
  selfUserId: string;
  onRole: (id: string, role: Role) => void;
  onActive: (id: string, active: boolean) => void;
  onReset: (id: string, email: string | null) => void;
}) {
  const columns = useMemo<ColDef<PlatformUser>[]>(() => [
    {
      field: "name", headerName: "Name", flex: 1, minWidth: 160,
      cellRenderer: (p: { data?: PlatformUser }) =>
        p.data ? (
          <Stack direction="row" spacing={0.75} useFlexGap
                 sx={{ alignItems: "center", minWidth: 0 }}>
            <Box component="span" sx={{ overflow: "hidden", textOverflow: "ellipsis" }}>
              {p.data.name}
            </Box>
            {p.data.user_id === selfUserId && (
              <StatusChip label="you" tone="info" dense
                          tip="Your own account. You cannot change your own role or deactivate yourself — an owner who could demote themselves could lock the organization out of its own settings." />
            )}
          </Stack>
        ) : null,
    },
    {
      field: "email", headerName: "Email", flex: 1.2, minWidth: 190,
      cellClass: "mono",
    },
    {
      field: "role", headerName: "Role", width: 210, flex: 0,
      // Sorted and filtered on the label people actually read, not on the
      // SALES_MANAGER enum behind it.
      valueGetter: (p) => (p.data ? ROLE_LABEL[p.data.role] : ""),
      cellRenderer: (p: { data?: PlatformUser }) => {
        const u = p.data;
        if (!u) return null;
        if (!canManage || u.user_id === selfUserId) {
          return (
            <Box>
              {ROLE_LABEL[u.role]}
              {u.role_changed_by && (
                <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
                  changed by {u.role_changed_by}
                </Typography>
              )}
            </Box>
          );
        }
        return (
          <TextField
            select
            size="small"
            value={u.role}
            onChange={(e) => onRole(u.user_id, e.target.value as Role)}
            aria-label={`Role for ${u.name}`}
            sx={{ width: "100%", "& .MuiInputBase-input": { py: 0.5, fontSize: 12.5 } }}
            helperText={u.role_changed_by ? `changed by ${u.role_changed_by}` : undefined}
            slotProps={{ formHelperText: { sx: { m: 0, fontSize: 10.5 } } }}
          >
            {(Object.keys(ROLE_LABEL) as Role[]).map((r) => (
              <MenuItem key={r} value={r}>{ROLE_LABEL[r]}</MenuItem>
            ))}
          </TextField>
        );
      },
    },
    {
      headerName: "Status", width: 150, flex: 0,
      headerTooltip: "“Must change” means a temporary password was issued and "
        + "has not been used yet. “No password” means the account was "
        + "provisioned without one and cannot sign in until it is reset.",
      valueGetter: (p) => {
        const u = p.data;
        if (!u) return "";
        return !u.active ? "deactivated"
          : !u.has_password ? "no password"
            : u.must_change_password ? "must change" : "active";
      },
      cellRenderer: (p: { data?: PlatformUser; value?: string }) => {
        const label = String(p.value ?? "");
        const tone = label === "deactivated" ? "neutral"
          : label === "active" ? "good" : "warn";
        return <StatusChip label={label} tone={tone} />;
      },
    },
    {
      field: "last_login_at", headerName: "Last sign-in", width: 165, flex: 0,
      cellStyle: { color: "var(--color-neutral-600)" },
      valueFormatter: (p) => when(p.value as string | null),
    },
    ...(canManage
      ? ([{
          headerName: "", width: 210, flex: 0, sortable: false, filter: false,
          resizable: false,
          cellRenderer: (p: { data?: PlatformUser }) => {
            const u = p.data;
            // Your own row carries no actions at all, which is the same rule
            // the role cell follows and the reason it is not merely disabled:
            // a greyed "Deactivate" on your own account reads as a permission
            // problem rather than as a deliberate boundary.
            if (!u || u.user_id === selfUserId) return null;
            return (
              <Stack direction="row" spacing={0.5} useFlexGap sx={{ flexWrap: "wrap" }}>
                <Button variant="text" size="small"
                        onClick={() => onReset(u.user_id, u.email)}>
                  Reset password
                </Button>
                <Button variant="text" size="small"
                        onClick={() => onActive(u.user_id, !u.active)}>
                  {u.active ? "Deactivate" : "Reactivate"}
                </Button>
              </Stack>
            );
          },
        }] as ColDef<PlatformUser>[])
      : []),
  ], [canManage, selfUserId, onRole, onActive, onReset]);

  return (
    <Box sx={{ mt: 2 }}>
      <DataGrid<PlatformUser>
        ariaLabel="People and roles"
        rows={users}
        columns={columns}
        pageSize={25}
        rowHeight={54}
        // Every write on this screen refetches the whole list, so without a
        // stable id ag-grid rebuilds the body and the role select somebody just
        // used loses focus mid-change.
        getRowId={(u) => u.user_id}
        // Dimmed, and the row also says "deactivated" in its Status chip — the
        // tint is never the only thing carrying it.
        rowClass={(u) => (u.active ? undefined : "ag-row-dimmed")}
        // No column filters: a team is tens of people, the columns are all
        // short, and sorting answers the questions this screen is asked.
        filters={false}
        empty={
          <EmptyState
            title="No accounts yet"
            reason="An owner creates accounts from the form above; each one is issued a temporary password shown once."
          />
        }
      />
    </Box>
  );
}

/* ── the AI layer, from the outside ───────────────────────────────────────── */

/** USD, to the precision the numbers actually have.
 *
 *  Not `money()`: that formats the organization's own currency, and provider
 *  rates are quoted in dollars. Showing a dollar figure with a rupee sign
 *  because a shared helper was handy would be the wrong kind of reuse. */
function usd(n: number): string {
  if (!n) return "$0.00";
  return n < 0.01 ? `$${n.toFixed(5)}` : `$${n.toFixed(2)}`;
}

const BAND_TONE: Record<string, Tone> = {
  OK: "good", HIGH: "bad", SUSPICIOUSLY_LOW: "warn", INSUFFICIENT_DATA: "neutral" };

/**
 * Is the AI on, what would the next run cost, and what has it cost so far.
 *
 * This panel exists because "AI-native" was a claim nobody could check from
 * inside the product. `AI_PROVIDER` defaults to an offline stand-in, so the
 * shipped default writes deterministic sentences and says nothing about it —
 * and the one place that knew was an environment variable on the server. Now
 * the first line of this panel says which provider is really running and why,
 * and the figures beside it say what turning the real one on would cost before
 * it is pointed at a real book.
 *
 * Everything here is read-only. Switching providers is a deployment decision
 * (a key, a restart), not a toggle a browser session should own.
 */
function AiLayerSection({ token }: { token: string }) {
  const [ready, setReady] = useState<AiReadiness | null>(null);
  const [metrics, setMetrics] = useState<AiMetricsReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    Promise.all([papi.aiReadiness(token), papi.aiMetrics(token)])
      .then(([r, m]) => { if (live) { setReady(r); setMetrics(m); setError(null); } })
      .catch((e) => { if (live) setError((e as Error).message); });
    return () => { live = false; };
  }, [token]);

  if (error) return <ErrorState title="The AI layer did not load" error={error} />;
  if (!ready) return <LoadingState rows={2} />;

  const w = metrics?.windows?.["7d"];
  const p = ready.provider;

  return (
    <Bp className="st-section">
      <h3>
        <Labelled tip="The narrative layer only. Every figure in a decision is computed deterministically and validated on the way out, whichever provider is running.">
          AI layer
        </Labelled>
      </h3>

      <Stack direction="row" spacing={1} sx={{ flexWrap: "wrap", rowGap: 1, mb: 1 }}>
        <StatusChip
          label={p.live ? `Live · ${p.model}` : "Offline stand-in"}
          tone={p.live ? "good" : "warn"}
          tip={p.live
            ? "Decision narratives are written by a model, from the computed facts."
            : "Decision narratives are deterministic text, not model output."}
        />
        <StatusChip label={`configured: ${p.configured}`} tone="neutral" />
        {w && (
          <StatusChip
            label={`health: ${w.health.band}`}
            tone={BAND_TONE[w.health.band] ?? "neutral"}
            tip={w.health.note}
          />
        )}
      </Stack>

      {p.detail && <p className="st-help">{p.detail}</p>}

      <Box sx={{
        display: "grid", gap: 2, mt: 2,
        gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))" }}>
        <MetricCard
          label="Next run"
          value={ready.would_call_provider}
          sub={`${ready.would_reuse_cached} cached, ${ready.would_suppress_up_front} withheld`}
          tip="Provider calls the next decision generation would make. Unchanged context is reused rather than re-inferred, and thin evidence is withheld without asking the model."
        />
        <MetricCard
          label="Next run costs"
          value={usd(ready.estimated_cost_usd)}
          sub={`${usd(ready.estimated_cost_per_decision_usd)} per decision`}
          tip={ready.note}
        />
        <MetricCard
          label="Spent, 7 days"
          value={usd(w?.cost.total_estimated ?? 0)}
          sub={w ? `${w.provider_calls} provider calls of ${w.calls}` : "no calls yet"}
          tip="Estimated from the token counts the provider reported, at the configured rates."
        />
        <MetricCard
          label="Median latency"
          value={w?.latency_ms.median != null ? `${w.latency_ms.median} ms` : "—"}
          sub={w?.latency_ms.max != null ? `${w.latency_ms.max} ms worst` : undefined}
          tip="How long a decision waits on the model. The deterministic signal is never blocked by it — a slow or failed call degrades to the template."
        />
        <MetricCard
          label="Degraded, 7 days"
          value={pct(w?.rates.degraded ?? 0)}
          sub="model output the gate refused"
          tip="A refused reading still surfaces the decision, with the signal's own numbers. Consistently high points at the prompt or the model; consistently zero means the gate is not doing anything."
        />
      </Box>

      <p className="st-help">
        Rates: {usd(ready.rates.per_mtok_input)} per million input tokens,{" "}
        {usd(ready.rates.per_mtok_output)} per million output, capped at{" "}
        {ready.rates.max_output_tokens_per_call} output tokens a call. {ready.note}
      </p>
    </Bp>
  );
}

export function SettingsScreen({ session }: { session: PlatformSession }) {
  const [users, setUsers] = useState<PlatformUser[]>([]);
  const [canManage, setCanManage] = useState(false);
  const [policy, setPolicy] = useState<OrgPolicy | null>(null);
  const [margin, setMargin] = useState<MarginPolicy | null>(null);
  const [fixed, setFixed] = useState<FixedThresholds | null>(null);
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
      setMargin(p.margin_policy);
      setFixed(p.fixed);
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

  // Reads the same table the nav reads, so "what this role is offered" has one
  // answer in the app. Note what is deliberately NOT moved here: `canManage`
  // below comes from the server's own `can_manage` on the users response, and
  // swapping a server answer for a client guess would be a downgrade however
  // tidy it looked.
  const isSales = !abilityFor(session).can("read", "economics");

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
          <Button type="submit" variant="outlined">Change password</Button>
          {pwMsg && <div className="st-span st-help">{pwMsg}</div>}
        </form>
      </Bp>

      {/* ── users and roles ── */}
      {!isSales && (
        <Bp className="st-section">
          <h3>
            <Labelled
              tip={
                <>
                  Roles are enforced by the server, not by hiding fields. A salesperson's
                  response contains no cost and no margin — <b>absent</b>, not masked, so
                  there is nothing to read out of the network tab.
                </>
              }
            >
              People and roles
            </Labelled>
          </h3>
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
              <Button variant="text" size="small" onClick={() => setIssued(null)}>Dismiss</Button>
            </div>
          )}

          <UsersGrid
            users={users}
            canManage={canManage}
            selfUserId={session.user_id}
            onRole={(id, role) => patchUser(id, { role })}
            onActive={(id, active) => patchUser(id, { active })}
            onReset={reset}
          />
        </Bp>
      )}

      {/* ── the AI layer ── */}
      {canManage && <AiLayerSection token={session.token} />}

      {/* ── approval policy ── */}
      {!isSales && policy && (
        <Bp className="st-section">
          <h3>
            <Labelled tip="Who signs off. The margin policy below decides what trips a request in the first place — these two settings are easy to confuse and do different jobs.">
              Approval policy
            </Labelled>
          </h3>
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

      {/* ── margin policy (owner-editable) ── */}
      {!isSales && margin && (
        <MarginPolicySection
          token={session.token}
          policy={margin}
          fixed={fixed}
          canManage={canManage}
          onSaved={(p) => setMargin(p)}
        />
      )}
    </div>
  );
}
