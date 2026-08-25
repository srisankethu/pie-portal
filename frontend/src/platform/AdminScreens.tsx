import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { ROLE_LABEL } from "./format";
import { formatDateTime, since } from "../when";
import { papi } from "./api";
import type { PlatformSessionRow } from "./api";
import type {
  AiByokProvider,
  AiByokView,
  AiMetricsReport,
  AiReadiness,
  ApprovalRequest,
  FixedThresholds,
  FloorBacktest,
  MarginPolicy,
  MarginPolicyPatch,
  OrgPolicy,
  PlatformSession,
  PlatformUser,
  PolicyField,
  RetainedPatRow,
  Role,
  ZohoConnection } from "./types";
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemText from "@mui/material/ListItemText";
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
            placeholder={
              req.requires_rationale
                ? "Why is this worth it? (required — this price is below cost)"
                : "Add a note (required to reject or return)"
            }
            value={note}
            onChange={(e) => setNote(e.target.value)}
            aria-label="Decision note"
          />
          <div className="ap-buttons">
            {/* Signing a below-cost line is the one irreversible concession here,
                and it was the only decision in the app that took no reason at
                all. The server refuses it too — this only stops the round trip. */}
            <Button
              variant="contained" size="small"
              disabled={busy || (req.requires_rationale && !note.trim())}
              onClick={() => act("APPROVED")}
            >
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
          {/* The server's own reason, when it gave one. Deriving the sentence
              from `required_authority` told a manager that their own
              manager-authority request was "waiting on a manager". */}
          {req.cannot_decide_reason
            ?? (isMine
              ? `Waiting on ${req.required_authority === "OWNER" ? "an owner" : "a manager"}.`
              : "You are not authorized to decide this one.")}
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

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
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
/** Retained-profit rows as the editor holds them: exactly the wire shape, so
 *  there is no conversion to get wrong. The amount stays text the whole way —
 *  it is money, and routing it through a `number` is how the last paise of a
 *  figure read off an audited account go missing. */
type Retained = RetainedPatRow[];

/** Kinds that are rows rather than a scalar, so they sit outside the generic
 *  number grid and carry their own editor. A field that landed in the grid by
 *  accident would be rendered as a box with `""` in it and saved as nothing. */
const ROW_KINDS = new Set(["family_margins", "retained_pat"]);

/** One row of a row-kind editor, with the control that removes it.
 *
 * The row *bodies* have nothing in common — family margins are two boxes,
 * retained profit is a company picker and two more — but the chrome around them
 * carries the one piece of logic that must not diverge: a reader who cannot
 * manage the policy gets no remove button. Written twice, that is two places
 * for the gate to be dropped, and the one that gets dropped is never the one
 * anybody re-reads. `ui-standards` §10, at its smallest honest size. */
function EditableRow({
  index, canManage, removeLabel, onRemove, children,
}: {
  index: number;
  canManage: boolean;
  removeLabel: string;
  onRemove: (index: number) => void;
  children: ReactNode;
}) {
  return (
    <span className="mp-family">
      {children}
      {canManage && (
        <button type="button" aria-label={`Remove ${removeLabel}`}
                onClick={() => onRemove(index)}>
          ×
        </button>
      )}
    </span>
  );
}

/** The control that appends a row. Same gate, same reason. */
function AddRow({ canManage, label, onAdd }: {
  canManage: boolean; label: string; onAdd: () => void;
}) {
  if (!canManage) return null;
  return (
    <Button type="button" variant="text" size="small" onClick={onAdd}>
      {label}
    </Button>
  );
}

function toRetained(f: PolicyField | undefined): Retained {
  if (!f) return [];
  return ((f.value as RetainedPatRow[]) || []).map(
    ([entity, year, amount]) => [entity, year, amount] as RetainedPatRow);
}

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

/** "What would this floor have done?" — asked before the floor is saved.
 *
 *  The replay existed as `python -m app.commercial.backtest` and nowhere else,
 *  so the one edit on this screen with a blast radius across every future quote
 *  was also the one edit nobody could model first. An owner could raise the
 *  approval floor two points and find out what that meant from the approvals
 *  queue over the following fortnight.
 *
 *  Three decisions worth stating:
 *
 *  **It is a button, not a live preview.** Each call replays every recorded
 *  quote line twice, against the baseline policy and the variant. Firing that
 *  on every keystroke in the margin box would be a full table scan per digit.
 *
 *  **It reads the draft, not the saved policy.** The whole value is answering
 *  the question before committing to it, so the numbers come from what the
 *  boxes currently say — and the panel disables itself when the ladder is out
 *  of order, because the server would refuse that policy anyway.
 *
 *  **The two "could not judge" counts sit with the findings, not under them.**
 *  Lines with no cost on record were not judged either way, and rows priced
 *  under a policy this replay cannot rebuild do not agree with what was
 *  recorded. Both are the §1 case: a count of what a replay found means nothing
 *  without what it could not look at.
 *
 *  Owner only, matching the endpoint. `shortfall` plus the margin the caller
 *  supplied gives cost in closed form, so this is not a surface to widen.
 */
function FloorBacktestPanel({ token, minMargin, marginFloor, ready }: {
  token: string;
  minMargin: number | undefined;
  marginFloor: number | undefined;
  ready: boolean;
}) {
  const [report, setReport] = useState<FloorBacktest | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async () => {
    if (minMargin === undefined) return;
    setBusy(true);
    setError(null);
    try {
      setReport(await papi.floorBacktest(token, minMargin, marginFloor ?? null));
    } catch (e) {
      setReport(null);
      setError(e instanceof Error ? e.message : "The replay could not be run.");
    } finally {
      setBusy(false);
    }
  }, [token, minMargin, marginFloor]);

  return (
    <Box sx={{ mt: 2 }}>
      <Stack direction="row" spacing={1.5}
             sx={{ alignItems: "center", flexWrap: "wrap" }}>
        <Button variant="outlined" size="small" onClick={run}
                disabled={!ready || busy || minMargin === undefined}>
          {busy ? "Replaying…" : "See what this would have done"}
        </Button>
        <Typography variant="caption" color="text.secondary">
          Replays every quote line already priced here against{" "}
          {minMargin === undefined ? "this floor" : `a ${pct(minMargin)} approval floor`}.
          Nothing is saved.
        </Typography>
      </Stack>

      {error && <Alert severity="error" sx={{ mt: 1.5 }}>{error}</Alert>}

      {report && (
        <Alert severity="info" icon={false} sx={{ mt: 1.5 }}>
          <AlertTitle>
            {report.newly_requires_approval} line
            {report.newly_requires_approval === 1 ? "" : "s"} would have needed a
            signature that did not
          </AlertTitle>
          <Typography variant="body2" component="div">
            Out of {report.lines_examined} priced here.
            {report.revenue_newly_gated
              ? ` They are worth ₹${report.revenue_newly_gated} between them.`
              : ""}
            {report.no_longer_requires_approval > 0
              ? ` ${report.no_longer_requires_approval} line(s) would no longer have needed one.`
              : ""}
          </Typography>

          {/* Beside the findings, never below them. A replay's counts are not
              readable without what it could not judge. */}
          {(report.unjudgeable_no_cost_on_record > 0
            || report.baseline_disagreements > 0) && (
            <Box component="ul" sx={{ mt: 1, mb: 0, pl: 2.5 }}>
              {report.unjudgeable_no_cost_on_record > 0 && (
                <Typography component="li" variant="body2">
                  <b>{report.unjudgeable_no_cost_on_record}</b> line(s) had no cost
                  on record and were not judged either way — not counted as
                  passing.
                </Typography>
              )}
              {report.baseline_disagreements > 0 && (
                <Typography component="li" variant="body2">
                  <b>{report.baseline_disagreements}</b> line(s) were priced under
                  a policy this replay cannot rebuild, so read the counts above
                  as covering the rest.
                </Typography>
              )}
            </Box>
          )}

          {report.by_customer.length > 0 && (
            <Typography variant="body2" sx={{ mt: 1 }}>
              Most affected: {report.by_customer.slice(0, 3)
                .map((c) => `${c.name} (${c.lines})`).join(", ")}.
            </Typography>
          )}
        </Alert>
      )}
    </Box>
  );
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
  const [retained, setRetained] = useState<Retained>([]);
  const [companies, setCompanies] = useState<ZohoConnection[]>([]);
  const [cleared, setCleared] = useState<string[]>([]);
  const [msg, setMsg] = useState<{ text: string; bad: boolean } | null>(null);
  const [busy, setBusy] = useState(false);

  // The connected companies, so a retained-profit row names "SLS Engineers"
  // rather than a connection id. Fetched here rather than threaded down: it is
  // the only field on this screen that needs them, and a failure degrades to a
  // plain text box rather than taking the section with it.
  useEffect(() => {
    let live = true;
    papi.listConnections(token)
      .then((v) => live && setCompanies(v.connections))
      .catch(() => undefined);
    return () => { live = false; };
  }, [token]);

  // Re-seed whenever the server's version of the policy changes, so a save (or
  // a reset) leaves the boxes showing what is actually in force.
  const reseed = useCallback(() => {
    const d: Draft = {};
    policy.fields.forEach((f) => {
      if (!ROW_KINDS.has(f.kind)) d[f.field] = toInput(f);
    });
    setDraft(d);
    setFamilies(toFamilies(byName.target_margin_by_family));
    setRetained(toRetained(byName.retained_pat));
    setCleared([]);
  }, [policy, byName]);

  useEffect(reseed, [reseed]);

  const scalars = policy.fields.filter((f) => !ROW_KINDS.has(f.kind));

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
    JSON.stringify(families) !== JSON.stringify(toFamilies(byName.target_margin_by_family)) ||
    JSON.stringify(retained) !== JSON.stringify(toRetained(byName.retained_pat));

  function reset(field: string) {
    const f = byName[field];
    if (!f) return;
    setCleared((c) => (c.includes(field) ? c : [...c, field]));
    if (f.kind === "retained_pat") {
      setRetained([]);   // the default is "nothing confirmed", and that is empty
    } else if (f.kind === "family_margins") {
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

      const patField = byName.retained_pat;
      if (
        patField &&
        !clear.includes("retained_pat") &&
        JSON.stringify(retained) !== JSON.stringify(toRetained(patField))
      ) {
        // Half-typed rows are dropped rather than sent. A row with no company
        // or no year is somebody mid-edit; the server would refuse it and the
        // message would land on the section rather than on the row.
        const rows = retained.filter(
          ([entity, year, amount]) =>
            entity.trim() && year.trim() && amount.trim());
        for (const [, year] of rows) {
          if (!/^FY\d{4}-\d{2}$/.test(year.trim())) {
            throw new Error(`"${year}" is not a financial year — write it as FY2025-26.`);
          }
        }
        // The amount is passed through as typed, commas and all: the server
        // normalises it in one place, and a second parser here is a second
        // answer to what "1,25,00,000" means.
        patch.retained_pat = rows.map(
          ([entity, year, amount]) =>
            [entity.trim(), year.trim(), amount.trim()] as RetainedPatRow);
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
              <EditableRow
                key={i} index={i} canManage={canManage}
                removeLabel={name || "family"}
                onRemove={(j) => setFamilies((fs) => fs.filter((_, k) => k !== j))}
              >
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
              </EditableRow>
            ))}
            <AddRow canManage={canManage} label="Add a family"
                    onAdd={() => setFamilies((fs) => [...fs, ["", ""]])} />
            {families.length === 0 && !canManage && (
              <span className="st-help">No family overrides — the default applies to everything.</span>
            )}
          </div>
        </div>
      )}

      {byName.retained_pat && (
        <div className="mp-field mp-wide">
          <label>
            <Labelled tip={byName.retained_pat.help}>
              {byName.retained_pat.label}
            </Labelled>
          </label>
          {/* Not a grid. The row count here is the number of legal entities
              times the years anybody has closed — set by the shape of the
              business, not by its size — so this is the fact-panel case
              ui-standards §3 names, and a DataGrid would be a filter bar over
              six rows. */}
          <div className="mp-families">
            {retained.map(([entity, year, amount], i) => (
              <EditableRow
                key={i} index={i} canManage={canManage}
                removeLabel={`row ${i + 1}`}
                onRemove={(j) => setRetained((rs) => rs.filter((_, k) => k !== j))}
              >
                {companies.length ? (
                  <TextField
                    select size="small" value={entity} disabled={!canManage}
                    sx={{ minWidth: 180 }}
                    label="Entity"
                    onChange={(e) =>
                      setRetained((rs) => rs.map((row, j) =>
                        j === i ? [e.target.value, row[1], row[2]] : row))}
                  >
                    {companies.map((c) => (
                      <MenuItem key={c.connection_id} value={c.connection_id}>
                        {c.label}
                      </MenuItem>
                    ))}
                  </TextField>
                ) : (
                  // The connections never loaded. A text box keeps the field
                  // usable rather than making the whole section depend on a
                  // second request having succeeded.
                  <input
                    className="input" style={{ width: 180 }} value={entity}
                    disabled={!canManage} aria-label={`Entity for row ${i + 1}`}
                    onChange={(e) =>
                      setRetained((rs) => rs.map((row, j) =>
                        j === i ? [e.target.value, row[1], row[2]] : row))}
                  />
                )}
                <input
                  className="input" style={{ width: 120 }} value={year}
                  placeholder="FY2025-26" disabled={!canManage}
                  aria-label={`Financial year for row ${i + 1}`}
                  onChange={(e) =>
                    setRetained((rs) => rs.map((row, j) =>
                      j === i ? [row[0], e.target.value, row[2]] : row))}
                />
                <span className="unit">{moneySymbol()}</span>
                <input
                  className="input" value={amount} inputMode="decimal"
                  disabled={!canManage}
                  aria-label={`Retained profit for row ${i + 1}`}
                  onChange={(e) =>
                    setRetained((rs) => rs.map((row, j) =>
                      j === i ? [row[0], row[1], e.target.value] : row))}
                />
              </EditableRow>
            ))}
            <AddRow canManage={canManage} label="Add a year"
                    onAdd={() => setRetained((rs) => [...rs, ["", "", ""]])} />
            {retained.length === 0 && (
              <span className="st-help">
                Nothing confirmed. The self-funding reading stays empty until
                every trading entity has a figure for a year — it will not stand
                in gross profit, which is a different and much larger number.
              </span>
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

      {/* Between the ladder and Save, which is where the question belongs:
          you have seen the floors line up, now ask what they would have cost
          before you commit them. Owner only, matching the endpoint. */}
      {canManage && (
        <FloorBacktestPanel
          token={token}
          minMargin={live.min_margin}
          marginFloor={live.margin_floor}
          ready={ladderOk} />
      )}

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
  users, canManage, selfUserId, onRole, onActive, onMember, onReset,
}: {
  users: PlatformUser[];
  canManage: boolean;
  selfUserId: string;
  onRole: (id: string, role: Role) => void;
  onActive: (id: string, active: boolean) => void;
  /** End or restore this person's membership of *this* organization. The
   *  second argument is the value to set, so `true` reinstates. */
  onMember: (id: string, member: boolean) => void;
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
        // Membership first, and deliberately: "removed" is the strongest
        // statement the column can make about this workspace — the person may
        // have a perfectly healthy login, and saying "active" because of it
        // would answer a question nobody on this screen is asking. A missing
        // value is ACTIVE, which is what it was before memberships existed.
        if (u.membership_status === "REMOVED") return "removed";
        if (u.membership_status === "INVITED") return "invited";
        return !u.active ? "deactivated"
          : !u.has_password ? "no password"
            : u.must_change_password ? "must change" : "active";
      },
      cellRenderer: (p: { data?: PlatformUser; value?: string }) => {
        const label = String(p.value ?? "");
        const tone = label === "deactivated" || label === "removed" ? "neutral"
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
            const removed = u.membership_status === "REMOVED";
            return (
              <Stack direction="row" spacing={0.5} useFlexGap sx={{ flexWrap: "wrap" }}>
                {/* Nothing to reset for somebody this workspace no longer
                    admits: their password is their own business now, and
                    offering it here would imply this organization still has a
                    say over an account it does not. */}
                {!removed && (
                  <Button variant="text" size="small"
                          onClick={() => onReset(u.user_id, u.email)}>
                    Reset password
                  </Button>
                )}
                {/* Removing is the John-leaves-Acme act, and it is separate
                    from deactivating on purpose: this closes one door, that
                    closes the account. A person who leaves one workspace and
                    keeps their own needs the first and would be wrecked by the
                    second. */}
                <Button variant="text" size="small"
                        onClick={() => onMember(u.user_id, removed)}>
                  {removed ? "Reinstate" : "Remove from organization"}
                </Button>
                {!removed && (
                  <Button variant="text" size="small"
                          onClick={() => onActive(u.user_id, !u.active)}>
                    {u.active ? "Deactivate" : "Reactivate"}
                  </Button>
                )}
              </Stack>
            );
          },
        }] as ColDef<PlatformUser>[])
      : []),
  ], [canManage, selfUserId, onRole, onActive, onMember, onReset]);

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
        rowClass={(u) => (u.active && u.membership_status !== "REMOVED"
          ? undefined : "ag-row-dimmed")}
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
 * The figures are read-only; the provider no longer is. An owner can bring the
 * organization's own key for Anthropic Claude, OpenAI, Google Gemini or
 * OpenRouter in the panel below and choose which one runs — the environment variables remain the
 * deployment-wide fallback. The key itself is write-only: the server stores it
 * encrypted and every response carries at most its last four characters.
 */
function AiLayerSection({ token }: { token: string }) {
  const [ready, setReady] = useState<AiReadiness | null>(null);
  const [metrics, setMetrics] = useState<AiMetricsReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    let live = true;
    Promise.all([papi.aiReadiness(token), papi.aiMetrics(token)])
      .then(([r, m]) => { if (live) { setReady(r); setMetrics(m); setError(null); } })
      .catch((e) => { if (live) setError((e as Error).message); });
    return () => { live = false; };
  }, [token]);

  useEffect(() => load(), [load]);

  if (error) return <ErrorState title="The AI layer did not load" error={error} />;
  if (!ready) return <LoadingState rows={2} />;

  const w = metrics?.windows?.["7d"];
  const p = ready.provider;
  // "Live" is a claim about configuration; whether calls succeed is a fact the
  // telemetry holds. A green chip over a key that fails every call is exactly
  // the green-chip-over-a-loss defect this codebase has been burned by, so the
  // chip and the banner below read the failure rate rather than assuming it.
  const failedCalls = w ? Math.round((w.rates.failed ?? 0) * w.calls) : 0;
  const allProviderCallsFailed =
    w != null && w.provider_calls > 0 && failedCalls >= w.provider_calls;

  return (
    <Bp className="st-section">
      <h3>
        <Labelled tip="The narrative layer only. Every figure in a decision is computed deterministically and validated on the way out, whichever provider is running.">
          AI layer
        </Labelled>
      </h3>

      <Stack direction="row" spacing={1} sx={{ flexWrap: "wrap", rowGap: 1, mb: 1 }}>
        <StatusChip
          label={p.live
            ? (allProviderCallsFailed ? `Live · ${p.model} — calls failing` : `Live · ${p.model}`)
            : "Offline stand-in"}
          tone={p.live ? (failedCalls > 0 ? "warn" : "good") : "warn"}
          tip={p.live
            ? (failedCalls > 0
              ? "A real provider is configured, but recent calls to it have failed — see the notice below."
              : "Decision narratives are written by a model, from the computed facts.")
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
        <MetricCard
          label="Failed, 7 days"
          value={pct(w?.rates.failed ?? 0)}
          sub="provider calls that errored"
          tip="The call itself failed — a bad key, an exhausted account, a network fault. The decision still surfaced with its deterministic signal, but nothing was phrased. Anything above zero deserves a look."
        />
      </Box>

      {failedCalls > 0 && (
        <Alert severity={allProviderCallsFailed ? "error" : "warning"} sx={{ mt: 2 }}>
          {failedCalls} of the last {w!.calls} AI-layer calls failed
          ({Object.entries(w!.failure_reasons)
            .map(([reason, n]) => `${reason} ×${n}`).join(", ")}).
          Those decisions fell back to their deterministic signals — nothing was
          hidden, but nothing was phrased. If an organization key is active
          below, test it; otherwise check the deployment&apos;s provider
          configuration.
        </Alert>
      )}

      <p className="st-help">
        Rates: {usd(ready.rates.per_mtok_input)} per million input tokens,{" "}
        {usd(ready.rates.per_mtok_output)} per million output, capped at{" "}
        {ready.rates.max_output_tokens_per_call} output tokens a call. {ready.note}
      </p>

      <ByokPanel token={token} onChanged={load} />
    </Bp>
  );
}

/* ── bring your own key ────────────────────────────────────────────────────── */

const PROVIDER_LABEL: Record<string, string> = {
  anthropic: "Anthropic Claude",
  openai: "OpenAI",
  gemini: "Google Gemini",
  openrouter: "OpenRouter",
};

/**
 * The AI layer's one writable surface: the organization's own provider keys.
 *
 * Server-enforced owner-only (`/api/v1/ai/*` is `require_owner`), and rendered
 * only inside the owner-gated AI section for the reason `ability.ts` gives —
 * the gate is the server's. Saving, testing and choosing all round-trip the
 * same view the GET returns, so the panel never has to guess at state.
 */
function ByokPanel({ token, onChanged }: { token: string; onChanged: () => void }) {
  const [view, setView] = useState<AiByokView | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    papi.aiProviders(token)
      .then((v) => { setView(v); setError(null); })
      .catch((e) => setError((e as Error).message));
  }, [token]);
  useEffect(() => { load(); }, [load]);

  // Every mutation returns the fresh view; readiness above changes with it.
  const apply = useCallback((v: AiByokView) => { setView(v); onChanged(); }, [onChanged]);

  if (error) return <ErrorState title="Provider keys did not load" error={error} />;
  if (!view) return <LoadingState rows={2} />;

  const chooseable = (p: AiByokProvider) => p.key_on_file || p.env_key_present;

  return (
    <Box sx={{ mt: 3 }}>
      <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
        <Labelled tip="A key entered here belongs to this organization and is billed to its own provider account. It is stored encrypted and never sent back to the browser — only its last four characters are shown.">
          Bring your own key
        </Labelled>
      </Typography>
      <p className="st-help">
        Run the narrative layer on this organization&apos;s own provider account.
        Leave the choice on the deployment default to use the server&apos;s
        configuration ({view.environment_provider}).
      </p>

      <TextField
        select size="small" sx={{ minWidth: 280, mb: 2 }}
        label="Runs the AI layer"
        // The empty string IS a real choice — the deployment default — and it
        // must read as one. MUI renders an empty value as a blank box even
        // with `displayEmpty`, so the text is supplied explicitly.
        slotProps={{
          select: {
            displayEmpty: true,
            renderValue: (v: unknown) => ((v as string)
              ? (PROVIDER_LABEL[v as string] ?? String(v))
              : `Deployment default (${view.environment_provider})`),
          },
          inputLabel: { shrink: true },
        }}
        value={view.active}
        onChange={(e) => {
          papi.setActiveAiProvider(token, e.target.value)
            .then(apply)
            .catch((err) => setError((err as Error).message));
        }}
      >
        <MenuItem value="">Deployment default ({view.environment_provider})</MenuItem>
        {view.providers.map((p) => (
          <MenuItem key={p.provider} value={p.provider} disabled={!chooseable(p)}>
            {PROVIDER_LABEL[p.provider] ?? p.provider}
            {!chooseable(p) ? " — no key" : ""}
          </MenuItem>
        ))}
      </TextField>

      {view.providers.map((p) => (
        <ByokProviderRow key={p.provider} token={token} row={p}
                         active={view.active === p.provider} onView={apply} />
      ))}
    </Box>
  );
}

function ByokProviderRow({ token, row, active, onView }: {
  token: string;
  row: AiByokProvider;
  active: boolean;
  onView: (v: AiByokView) => void;
}) {
  const [key, setKey] = useState("");
  const [model, setModel] = useState(row.model);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Removing a key cannot be undone — the key is write-only, so there is
  // nothing to restore it from — and if this provider runs the decisions,
  // removal also silently reverts the AI layer to the deployment default.
  // One misclick next to "Test" must not do that, so the button arms first.
  const [armed, setArmed] = useState(false);

  async function run(work: () => Promise<void>) {
    setBusy(true);
    setMsg(null);
    try {
      await work();
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const save = () => run(async () => {
    const v = await papi.saveAiKey(token, row.provider, { api_key: key, model });
    onView(v);
    setKey("");
    setMsg("Key saved.");
  });

  const test = () => run(async () => {
    const r = await papi.testAiKey(token, row.provider);
    setMsg(r.ok ? `Works — ${r.model} answered.` : `Failed: ${r.detail}`);
  });

  const remove = () => {
    if (!armed) {
      setArmed(true);
      setMsg(active
        ? "Removing this key also puts decisions back on the deployment default. Click again to confirm."
        : "The key cannot be shown again once removed. Click again to confirm.");
      return;
    }
    setArmed(false);
    run(async () => {
      const v = await papi.removeAiKey(token, row.provider);
      onView(v);
      setModel("");
      setMsg("Key removed.");
    });
  };

  return (
    <Box sx={{ py: 1.5, borderTop: 1, borderColor: "divider" }}>
      <Stack direction="row" spacing={1} sx={{ alignItems: "center", flexWrap: "wrap", rowGap: 1 }}>
        <Typography sx={{ fontWeight: 600, minWidth: 140 }}>
          {PROVIDER_LABEL[row.provider] ?? row.provider}
        </Typography>
        {active && <StatusChip label="runs decisions" tone="good" />}
        {row.key_on_file
          ? <StatusChip label={`key on file ····${row.key_hint}`} tone="neutral"
                        tip={row.rotated_at
                          ? `Last rotated ${formatDateTime(row.rotated_at)}`
                          : "Entered once; rotate by saving a new key."} />
          : row.env_key_present
            ? <StatusChip label="deployment key" tone="neutral"
                          tip="The server's environment holds a key for this provider; this organization has not entered its own." />
            : <StatusChip label="no key" tone="warn" />}
      </Stack>

      <Stack direction="row" spacing={1} sx={{ mt: 1, alignItems: "center", flexWrap: "wrap", rowGap: 1 }}>
        <TextField
          size="small" type="password" label="API key" value={key}
          autoComplete="off"
          placeholder={row.key_on_file ? "enter a new key to rotate" : ""}
          onChange={(e) => setKey(e.target.value)}
          sx={{ minWidth: 260 }}
        />
        <TextField
          size="small" label="Model" value={model}
          placeholder={row.default_model}
          helperText=""
          onChange={(e) => setModel(e.target.value)}
          sx={{ minWidth: 200 }}
        />
        <Button variant="outlined" size="small" disabled={busy || !key.trim()} onClick={save}>
          {row.key_on_file ? "Rotate" : "Save"}
        </Button>
        <Button variant="text" size="small"
                disabled={busy || !(row.key_on_file || row.env_key_present)} onClick={test}>
          Test
        </Button>
        {row.key_on_file && (
          <Button variant={armed ? "outlined" : "text"} size="small" color="error"
                  disabled={busy} onClick={remove}
                  onBlur={() => { if (armed) { setArmed(false); setMsg(null); } }}>
            {armed ? "Confirm removal" : "Remove"}
          </Button>
        )}
      </Stack>
      {msg && <p className="st-help">{msg}</p>}
    </Box>
  );
}

/** Turn a User-Agent into something a person can recognise their own device in.
 *
 *  Deliberately crude. The goal is "is this the laptop or the phone?", which two
 *  words answer; anything more precise means parsing a string that browsers have
 *  spent thirty years making unparseable, and a wrong-but-confident "Safari on
 *  iPhone" is worse than an honest "Unknown device" next to a sign-in time. */
export function describeDevice(ua: string): string {
  if (!ua.trim()) return "Unknown device";
  const browser =
    /\bEdg\//.test(ua) ? "Edge"
    : /\bOPR\/|\bOpera\b/.test(ua) ? "Opera"
    // Chrome's UA contains "Safari", so Safari is only Safari when Chrome is absent.
    : /\bChrome\/|\bCriOS\//.test(ua) ? "Chrome"
    : /\bFirefox\/|\bFxiOS\//.test(ua) ? "Firefox"
    : /\bSafari\//.test(ua) ? "Safari"
    : null;
  const platform =
    /\bAndroid\b/.test(ua) ? "Android"
    : /\biPhone\b|\biPad\b|\biOS\b/.test(ua) ? "iOS"
    : /\bWindows\b/.test(ua) ? "Windows"
    : /\bMac OS X\b|\bMacintosh\b/.test(ua) ? "macOS"
    : /\bLinux\b/.test(ua) ? "Linux"
    : null;
  if (browser && platform) return `${browser} on ${platform}`;
  return browser ?? platform ?? "Unknown device";
}

/** Where this account is signed in, and how to end any of it.
 *
 *  Exists because signing out became a real thing the server does. Before, the
 *  token was valid for its full thirty days whatever the browser did with its
 *  copy, so there was nothing true to put on a screen — a list of sessions none
 *  of which could be ended would have been decoration. Now each row is a
 *  revocable record, and this is the control for it.
 *
 *  A `List` rather than a `DataGrid`, per ui-standards §3: the row count here is
 *  set by how many devices one person signs in from, not by the size of the
 *  business. Three rows and an action each is not a grid. */
function SessionsSection({ onSignedOutEverywhere }: { onSignedOutEverywhere: () => void }) {
  const [rows, setRows] = useState<PlatformSessionRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRows(await papi.sessions());
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  async function revoke(sessionId: string) {
    setBusy(sessionId);
    try {
      await papi.revokeSession(sessionId);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function signOutEverywhere() {
    setBusy("all");
    try {
      await papi.logoutAll();
      // Ends this session too, by design — so the shell has to be told rather
      // than left drawing a signed-in frame over a dead cookie.
      onSignedOutEverywhere();
    } catch (e) {
      setError((e as Error).message);
      setBusy(null);
    }
  }

  return (
    <Bp className="st-section">
      <h3>
        <Labelled
          tip={
            <>
              Each row is a session on the server, not a browser's memory of one.
              Ending one here stops the credential working immediately, on that
              device, whether or not anybody still has the browser open.
            </>
          }
        >
          Where you are signed in
        </Labelled>
      </h3>
      <p className="st-help">
        Sessions end on their own after 12 hours unused, and after 30 days however
        much they are used. End one early if you do not recognise it.
      </p>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

      {rows === null && !error && <LoadingState rows={2} />}

      {rows !== null && rows.length === 0 && (
        <EmptyState
          title="No other sessions"
          reason="This is the only device signed in to this account."
        />
      )}

      {rows !== null && rows.length > 0 && (
        <>
          <List dense disablePadding>
            {rows.map((r) => (
              <ListItem
                key={r.session_id}
                disableGutters
                secondaryAction={
                  r.current ? null : (
                    <Button
                      size="small"
                      variant="text"
                      disabled={busy !== null}
                      onClick={() => void revoke(r.session_id)}
                    >
                      {busy === r.session_id ? "Ending…" : "Sign out"}
                    </Button>
                  )
                }
              >
                <ListItemText
                  primary={
                    <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
                      <Typography component="span" variant="body2">
                        {describeDevice(r.user_agent)}
                      </Typography>
                      {/* A chip, not coloured text — ui-standards §6. */}
                      {r.current && <StatusChip label="this device" tone="info" dense />}
                    </Stack>
                  }
                  secondary={
                    <>
                      Last used {since(r.last_seen_at)} · signed in{" "}
                      {formatDateTime(r.issued_at)}
                    </>
                  }
                />
              </ListItem>
            ))}
          </List>

          <Box sx={{ mt: 2 }}>
            <Button
              variant="outlined"
              color="error"
              disabled={busy !== null}
              onClick={() => void signOutEverywhere()}
            >
              {busy === "all" ? "Signing out…" : "Sign out everywhere"}
            </Button>
            <p className="st-help">
              Ends every session including this one, so you will be asked to sign in
              again. Changing your password does the same thing.
            </p>
          </Box>
        </>
      )}
    </Bp>
  );
}

export function SettingsScreen(
  { session, onToken, onSignedOutEverywhere }: {
    session: PlatformSession;
    onToken: (token: string) => void;
    /** "Sign out everywhere" ends this session too, so the shell must be told
     *  rather than left drawing a signed-in frame over a dead cookie. */
    onSignedOutEverywhere: () => void;
  },
) {
  const [users, setUsers] = useState<PlatformUser[]>([]);
  const [canManage, setCanManage] = useState(false);
  const [policy, setPolicy] = useState<OrgPolicy | null>(null);
  const [margin, setMargin] = useState<MarginPolicy | null>(null);
  const [fixed, setFixed] = useState<FixedThresholds | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [issued, setIssued] = useState<{ email: string; password: string } | null>(null);
  const [pw, setPw] = useState({ current: "", next: "" });
  const [pwMsg, setPwMsg] = useState<string | null>(null);

  // Mirrors `require_manager_or_owner`, which guards both calls below. A
  // salesperson used to fetch them anyway and get the 403 detail string rendered
  // as a red banner above their own account panel — an API error where a sentence
  // belonged. Don't ask for what this role cannot have.
  const mayReadOrg = abilityFor(session).can("read", "economics");

  const load = useCallback(async () => {
    if (!mayReadOrg) return;    // the account panel below needs nothing from the server
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
  }, [session.token, mayReadOrg]);

  useEffect(() => {
    load();
  }, [load]);

  async function patchUser(id: string,
                           body: { role?: Role; active?: boolean; member?: boolean }) {
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
      // The new token must replace the one in the session, not be discarded.
      // Changing a password stamps `password_changed_at`, and the server then
      // rejects every token minted before it — so keeping the old one signs the
      // user out by their own success, into a shell that still looks live
      // because nothing here tells it otherwise. `ForcedPasswordChange` has
      // always done this; only this screen forgot.
      const { token } = await papi.changeOwnPassword(session.token, pw.current, pw.next);
      onToken(token);
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
  const isSales = !mayReadOrg;

  return (
    <div className="dp-screen">
      <div className="dp-screen-head">
        <div>
          <h2>Settings</h2>
          <p className="text-muted">
            {canManage
              ? "You are the owner: accounts, roles and approval policy are yours."
              : isSales
                ? "Your account. Organization settings are the owner's."
                : "Your account, and how this organization is configured."}
          </p>
        </div>
      </div>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

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
          {/* The account this form is about. A change-password form with two
              password fields and nothing else gives a password manager no
              identity to file the new secret under, so it saves it against
              nothing or overwrites the wrong entry — and the person is locked
              out of the thing they just secured. Readonly rather than hidden:
              `autocomplete="username"` on a `display: none` field is ignored by
              some managers, and saying which account is about to change its
              password is worth a line on screen anyway. */}
          {session.email && (
            <TextField
              className="st-span" size="small" label="Account"
              value={session.email} autoComplete="username"
              slotProps={{ input: { readOnly: true } }} />
          )}
          <TextField size="small" type="password" label="Current password"
                     autoComplete="current-password" value={pw.current}
                     onChange={(e) => setPw({ ...pw, current: e.target.value })}
                     required />
          <TextField size="small" type="password" label="New password"
                     helperText="At least 10 characters"
                     autoComplete="new-password" value={pw.next}
                     onChange={(e) => setPw({ ...pw, next: e.target.value })}
                     required />
          <Button type="submit" variant="outlined">Change password</Button>
          {pwMsg && <div className="st-span st-help">{pwMsg}</div>}
        </form>
      </Bp>

      {/* ── where you are signed in ──
          Directly under the account panel because it belongs to the same
          question — this is your account and what is currently holding it — and
          because the password form above is the other half of the same answer:
          changing a password ends every session too. Shown to every role: a
          salesperson's sessions are as much theirs to end as an owner's. */}
      <SessionsSection onSignedOutEverywhere={onSignedOutEverywhere} />

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
            onMember={(id, member) => patchUser(id, { member })}
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
