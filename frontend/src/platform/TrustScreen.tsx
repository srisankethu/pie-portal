/**
 * The trust surface — what a customer can check about their own data.
 *
 * `backend/app/routers/trust.py` has served six owner-scoped endpoints since it
 * was written and no screen has ever called one of them. That is the whole
 * defect this fixes, and it is worse than an ordinary missing feature: the
 * router's own docstring says "a control the customer cannot observe is an
 * internal process, and internal processes are what they are being asked to take
 * on faith". Every promise it makes checkable was, in practice, still on faith.
 *
 * Three questions, one screen, in the order an owner asks them:
 *
 *   1. What leaves here for a model, and what never does?
 *   2. Who from the vendor has opened my data, when, and why?
 *   3. How do I take it elsewhere, or destroy it?
 *
 * The screen states the server's own words wherever the server has words —
 * `never_sent`, the access-log note, the erasure method. Paraphrasing them here
 * would create a second version of the promise, and the second version is the
 * one that drifts.
 *
 * Deliberately **not** here: the `ai-metrics` panel. `AI_PROVIDER` defaults to
 * `mock`, so cost is zero and the health band is `INSUFFICIENT_DATA` whatever
 * the book looks like — a screen of zeros teaches an owner that this screen is
 * not worth opening. It belongs beside this content once a real provider is
 * configured, as a fourth section: `papi.aiMetrics` already exists and is
 * likewise unused.
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import { formatDateTime } from "../when";
import { abilityFor } from "./ability";
import { papi } from "./api";
import { DataGrid, type ColDef } from "./DataGrid";
import { saveJson } from "./download";
import {
  EmptyState, ErrorState, LoadingState, SectionHeader, StatusChip, type Tone,
} from "./kit";
import type {
  AccessEventRow, AccessReport, DisclosureStatement, ErasureState, PayloadsReport,
  PlatformSession,
} from "./types";

/** What each break-glass action is called, and how loud it is.
 *
 *  A grant and a use are different events and the log has always distinguished
 *  them in `action`; the screen has to, or a window opened once and used forty
 *  times reads as forty grants. `ACCESSED` is deliberately the quiet one — it is
 *  the expected consequence of a grant somebody already approved, and colouring
 *  every use as an alarm is how a log stops being read.
 */
const ACTION: Record<string, { label: string; tone: Tone; tip: string }> = {
  GRANTED: {
    label: "Access granted", tone: "warn",
    tip: "A window was opened, with the stated reason. It expires on its own.",
  },
  ACCESSED: {
    label: "Opened", tone: "neutral",
    tip: "One use inside a granted window. There is one row per use, naming what was opened.",
  },
  REVOKED: {
    label: "Access revoked", tone: "good",
    tip: "The window was closed — either deliberately or because it expired.",
  },
};

function actionOf(action: string) {
  return ACTION[action] ?? { label: action, tone: "info" as Tone,
                             tip: "An action this screen does not have a name for yet." };
}

/* ── 1 · what reaches a model ─────────────────────────────────────────────── */

function DisclosurePanel({
  statement, payloads,
}: { statement: DisclosureStatement; payloads: PayloadsReport | null }) {
  const live = statement.provider !== "mock";
  return (
    <Paper variant="outlined" sx={{ p: 3, mb: 3 }}>
      <SectionHeader
        level="section"
        title="What reaches a model"
        sub="Served from the same constants the outbound checker measures against, so this statement and the behaviour cannot drift apart without a test failing."
      />

      <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: "wrap", mb: 2 }}>
        {/* The provider, not the model. `disclosure.statement()` reports
            `AI_MODEL` whatever the provider is, so on the default configuration
            it names a model nothing ever calls. Saying which provider is running
            is true in both configurations; naming the model is only true in one,
            so it is shown only when something would actually call it. */}
        <StatusChip
          label={live ? `Provider: ${statement.provider}` : "No model is called"}
          tone={live ? "info" : "neutral"}
          tip={live
            ? "A real provider is configured, so the payloads below left this machine."
            : "AI_PROVIDER is `mock`: the interpretation is generated offline from the signal's own figures, and nothing is sent anywhere."}
        />
        {live && <StatusChip label={statement.model} tone="neutral" tip="The configured model." />}
        <StatusChip
          label={statement.training_on_customer_data
            ? "Used for training" : "Never used for training"}
          tone={statement.training_on_customer_data ? "bad" : "good"} />
        <StatusChip
          label={statement.zero_retention_requested
            ? "Zero retention requested" : "Retention not requested"}
          tone={statement.zero_retention_requested ? "good" : "warn"}
          tip="Whether the platform asks the provider not to retain what it is sent." />
      </Stack>

      <Typography variant="body2" color="text.secondary" sx={{ mb: 3, maxWidth: "80ch" }}>
        {statement.notes}
      </Typography>

      {/* Six rows and six rows, fixed by the constants above — a fact panel, not
          a grid. `DataGrid.tsx` draws that line by whether the row count is set
          by the size of the business, and these two are set by the code. */}
      <Typography variant="subtitle2" sx={{ mb: 1 }}>May be sent</Typography>
      <Box component="table" className="facttable" sx={{ width: "100%", mb: 3 }}>
        <thead>
          <tr><th>Category</th><th>Example</th><th>Why it has to go</th></tr>
        </thead>
        <tbody>
          {statement.allowed.map((a) => (
            <tr key={a.category}>
              <td>{a.category}</td>
              <td className="mono">{a.example}</td>
              <td className="text-muted">{a.why}</td>
            </tr>
          ))}
        </tbody>
      </Box>

      <Typography variant="subtitle2" sx={{ mb: 1 }}>Never sent</Typography>
      <Box component="ul" sx={{ pl: 3, mb: 3, "& li": { mb: 0.5 } }}>
        {statement.never_sent.map((n) => (
          <Typography component="li" key={n} variant="body2">{n}</Typography>
        ))}
      </Box>

      {payloads && <PayloadCheck payloads={payloads} />}
    </Paper>
  );
}

/** Whether the list above is a claim or a measurement.
 *
 *  `findings_summary`'s own docstring: a non-zero flagged count "is a defect
 *  report, not a statistic to display and forget". So a flag is an error Alert
 *  naming the payloads, not a number in a row of numbers.
 */
function PayloadCheck({ payloads }: { payloads: PayloadsReport }) {
  const { payloads: total, flagged } = payloads.summary;
  if (total === 0) {
    return (
      <Alert severity="info">
        <AlertTitle>Nothing has been sent yet</AlertTitle>
        No model payload has been logged for this organization, so there is
        nothing here to check the statement against. This is the expected state
        while the provider is `mock`.
      </Alert>
    );
  }
  if (flagged === 0) {
    return (
      <Alert severity="success">
        <AlertTitle>{total} payload{total === 1 ? "" : "s"} checked, none flagged</AlertTitle>
        Every payload logged for this organization was scanned for the values in
        the &ldquo;never sent&rdquo; list above. None contained one.
      </Alert>
    );
  }
  const bad = payloads.payloads.filter((p) => p.findings.length > 0);
  return (
    <Alert severity="error">
      <AlertTitle>
        {flagged} of {total} payloads contained something the statement says never leaves
      </AlertTitle>
      This is a defect, not a statistic. Each payload below is named with what
      the checker found in it.
      <Box component="ul" sx={{ pl: 3, mt: 1, mb: 0 }}>
        {bad.map((p) => (
          <Typography component="li" key={p.payload_id} variant="body2">
            <span className="mono">{p.payload_id}</span>
            {p.decision_type ? ` · ${p.decision_type}` : ""}
            {" — "}{p.findings.join(", ")}
          </Typography>
        ))}
      </Box>
    </Alert>
  );
}

/* ── 2 · who has opened your data ─────────────────────────────────────────── */

/** The centrepiece. One row per event, not per window: a grant, each use inside
 *  it, and the revocation are separate rows because they are separate facts, and
 *  a log that collapsed them could not answer "how many times". */
function AccessPanel({ report }: { report: AccessReport }) {
  const columns = useMemo<ColDef<AccessEventRow>[]>(() => [
    {
      field: "at", headerName: "When", width: 190, flex: 0, sort: "desc",
      filter: "agTextColumnFilter",
      valueFormatter: (p) => formatDateTime(p.value as string | null),
    },
    {
      field: "action", headerName: "What happened", width: 170, flex: 0,
      filter: "agTextColumnFilter",
      cellRenderer: (p: { value: string }) => {
        const a = actionOf(p.value);
        return <StatusChip label={a.label} tone={a.tone} tip={a.tip} />;
      },
    },
    {
      field: "staff_user_id", headerName: "Who", width: 200, flex: 0,
      filter: "agTextColumnFilter",
      headerTooltip: "The vendor staff account, not one of your own users.",
    },
    {
      // The justification and the resource share a column because they are the
      // same question at two moments — why the window was opened, and what was
      // opened inside it — and the server puts both in `detail`.
      field: "detail", headerName: "Reason, or what was opened", flex: 1, minWidth: 260,
      filter: "agTextColumnFilter",
      valueFormatter: (p) => (p.value ? String(p.value) : "—"),
    },
  ], []);

  return (
    <Paper variant="outlined" sx={{ p: 3, mb: 3 }}>
      <SectionHeader
        level="section"
        title="Who has opened your data"
        sub="Every break-glass grant, every use inside it, and every revocation. There is no filter on this endpoint and no way to ask for a redacted subset."
      />
      <DataGrid<AccessEventRow>
        ariaLabel="Staff access to this organization"
        rows={report.events}
        getRowId={(r) => r.event_id}
        columns={columns}
        pageSize={25}
        empty={
          <EmptyState
            title="Nobody has opened your data"
            reason="No staff grant, use or revocation has ever been recorded against this organization. This is the log itself saying so, not an absence of logging." />
        }
        renderNarrow={(r) => {
          const a = actionOf(r.action);
          return (
            <Paper variant="outlined" sx={{ p: 2 }}>
              <Stack direction="row" spacing={1} sx={{ mb: 0.5, alignItems: "center" }}>
                <StatusChip label={a.label} tone={a.tone} />
                <Typography variant="caption" color="text.secondary">
                  {formatDateTime(r.at)}
                </Typography>
              </Stack>
              <Typography variant="body2">{r.staff_user_id}</Typography>
              <Typography variant="body2" color="text.secondary">
                {r.detail || "—"}
              </Typography>
            </Paper>
          );
        }}
      />
      <Typography variant="body2" color="text.secondary" sx={{ mt: 2, maxWidth: "80ch" }}>
        {report.note}
      </Typography>
    </Paper>
  );
}

/* ── 3 · take it elsewhere, or destroy it ─────────────────────────────────── */

function ExportPanel({ token }: { token: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function download() {
    setBusy(true);
    setError(null);
    try {
      // Built in the browser from the response rather than opening the endpoint
      // in a tab: the endpoint needs an Authorization header, and a plain link
      // to it downloads a 401. See `download.ts` — the skipped-rows export on
      // the data screen has the same constraint and shares this.
      saveJson(await papi.trustExport(token), "pie-portal-export.json");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Paper variant="outlined" sx={{ p: 3, mb: 3 }}>
      <SectionHeader
        level="section"
        title="Take your data elsewhere"
        sub="Everything this organization owns, as JSON — including the cross-connector identity graph, which no single source system holds."
      />
      {error && <Box sx={{ mb: 2 }}><ErrorState error={error} /></Box>}
      <Button variant="outlined" onClick={download} disabled={busy}>
        {busy ? "Preparing…" : "Download export"}
      </Button>
    </Paper>
  );
}

/** The only irreversible action in the application.
 *
 *  The server demands the organization id in the body and calls that "a
 *  deliberate friction, not a security control" — the role check is the control.
 *  This screen keeps the friction honest: the button is disabled until the id
 *  matches and a reason long enough to be a reason has been written, and the
 *  paragraph above it says what stops working, in the plain terms somebody about
 *  to do it needs. A confirmation that only says "are you sure?" is a
 *  confirmation somebody clicks through.
 */
function ErasurePanel({
  token, organizationId, state, onDone,
}: {
  token: string;
  organizationId: string;
  state: ErasureState;
  onDone: () => void;
}) {
  const [confirmId, setConfirmId] = useState("");
  const [reason, setReason] = useState("");
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (state.erased && state.receipt) {
    const r = state.receipt;
    return (
      <Paper variant="outlined" sx={{ p: 3, mb: 3 }}>
        <SectionHeader
          level="section"
          title="This organization has been erased"
          sub="The data key was destroyed, so everything encrypted under it is unreadable everywhere it exists — including in backups that cannot be selectively edited." />
        <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: "wrap", mb: 2 }}>
          <StatusChip
            label={r.verified ? "Receipt signature verified" : "Receipt signature does NOT verify"}
            tone={r.verified ? "good" : "bad"}
            tip="Re-checked on the server on every read, so the receipt is not believed merely because it exists." />
          {state.key_destroyed === false && (
            <StatusChip label="Key still present" tone="bad"
                        tip="A receipt exists but the data key was not destroyed. That is a defect — report it." />
          )}
        </Stack>
        <Box component="table" className="facttable" sx={{ width: "100%" }}>
          <tbody>
            <tr><td>Erased</td><td>{formatDateTime(r.erased_at)}</td></tr>
            <tr><td>Reason given</td><td>{r.reason}</td></tr>
            <tr><td>Requested by</td><td>{r.actor_user_id || "—"}</td></tr>
            <tr><td>Method</td><td>{r.method}</td></tr>
            <tr>
              <td>Signature</td>
              <td className="mono" style={{ wordBreak: "break-all" }}>{r.signature}</td>
            </tr>
          </tbody>
        </Box>
      </Paper>
    );
  }

  const idMatches = confirmId.trim() === organizationId;
  // 10 is the server's own `min_length` on `reason`. Mirrored rather than
  // guessed, so the button and the endpoint agree about what is enough.
  const reasonEnough = reason.trim().length >= 10;

  async function erase() {
    setBusy(true);
    setError(null);
    try {
      await papi.erase(token, confirmId.trim(), reason.trim());
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Paper variant="outlined" sx={{ p: 3, mb: 3 }}>
      <SectionHeader
        level="section"
        title="Destroy this organization's data"
        sub="Irreversible, and not undone by a restore from backup." />

      <Alert severity="warning" sx={{ mb: 2 }}>
        <AlertTitle>What this does</AlertTitle>
        It destroys the encryption key this organization&rsquo;s data is held
        under. Customer and item names, and every payload ever sent to a model,
        become permanently unreadable — in this database and in every backup of
        it, because a backup cannot be selectively edited. Decisions, signals and
        figures that were computed from those records remain as rows, without the
        names. Nobody, including the vendor, can reverse this.
      </Alert>

      {error && <Box sx={{ mb: 2 }}><ErrorState error={error} /></Box>}

      {!armed ? (
        <Button variant="outlined" color="error" onClick={() => setArmed(true)}>
          I want to erase this organization
        </Button>
      ) : (
        <Stack spacing={2} sx={{ maxWidth: "60ch" }}>
          <TextField
            label="Type this organization's id to confirm"
            placeholder={organizationId}
            value={confirmId}
            onChange={(e) => setConfirmId(e.target.value)}
            error={confirmId.length > 0 && !idMatches}
            helperText={
              confirmId.length > 0 && !idMatches
                ? `That is not this organization's id. It is "${organizationId}".`
                : `Exactly "${organizationId}".`
            }
            size="small" />
          <TextField
            label="Why"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            multiline minRows={2}
            error={reason.length > 0 && !reasonEnough}
            helperText="Recorded on the receipt, which is signed and kept after the data is gone. At least ten characters."
            size="small" />
          <Stack direction="row" spacing={1}>
            <Button
              variant="contained" color="error"
              disabled={!idMatches || !reasonEnough || busy}
              onClick={erase}>
              {busy ? "Erasing…" : "Erase permanently"}
            </Button>
            <Button variant="text" onClick={() => { setArmed(false); setConfirmId(""); setReason(""); }}>
              Cancel
            </Button>
          </Stack>
        </Stack>
      )}
    </Paper>
  );
}

/* ── the screen ───────────────────────────────────────────────────────────── */

export function TrustScreen({ session }: { session: PlatformSession }) {
  const { token, organization_id: organizationId } = session;
  // The nav item is owner-only, and the nav is not the only way in: a URL that
  // was bookmarked, shared or typed reaches this component whatever the role.
  // Without this, a manager at `#/trust` got a screen titled "Your data" with a
  // red "Owner role required" on it — which is what `ability.ts` says it exists
  // to prevent, since it reads as a broken product rather than a closed door.
  // The server still refuses; this only decides what to *offer*.
  const mayRead = abilityFor(session).can("read", "trust");

  const [statement, setStatement] = useState<DisclosureStatement | null>(null);
  const [payloads, setPayloads] = useState<PayloadsReport | null>(null);
  const [access, setAccess] = useState<AccessReport | null>(null);
  const [erasure, setErasure] = useState<ErasureState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    // Not fetched at all for a role that cannot read it. Four requests that all
    // 403 would put four failures in the console and one red box on screen, to
    // establish something the role already answered.
    if (!mayRead) { setLoading(false); return; }
    setLoading(true);
    try {
      const [d, p, a, e] = await Promise.all([
        papi.disclosure(token),
        papi.payloads(token),
        papi.accessLog(token),
        papi.erasureState(token),
      ]);
      setStatement(d);
      setPayloads(p);
      setAccess(a);
      setErasure(e);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [token, mayRead]);

  useEffect(() => { load(); }, [load]);

  if (!mayRead) {
    return (
      <div className="dp-screen">
        <SectionHeader
          title="Your data"
          sub="What leaves here for a model, who from the vendor has opened your books, and how to take your data elsewhere or destroy it." />
        <EmptyState
          title="This screen belongs to the owner"
          reason="Who has looked at this organization's data, what left for a model, and whether it can be destroyed are questions about the relationship with the vendor rather than about running the desk — so they are answered to the owner only. Nothing here is hidden from you by this browser: the server refuses these requests for any other role." />
      </div>
    );
  }

  return (
    <div className="dp-screen">
      <SectionHeader
        title="Your data"
        sub="What leaves here for a model, who from the vendor has opened your books, and how to take your data elsewhere or destroy it." />

      {/* "We could not look" is never dressed as "there is nothing to see" —
          the error replaces the sections rather than sitting above empty ones. */}
      {error ? (
        <ErrorState error={error} onRetry={load} busy={loading} />
      ) : loading && !statement ? (
        <LoadingState rows={4} height={120} label="Reading the trust surface…" />
      ) : (
        <>
          {statement && <DisclosurePanel statement={statement} payloads={payloads} />}
          {access && <AccessPanel report={access} />}
          <ExportPanel token={token} />
          {erasure && (
            <ErasurePanel token={token} organizationId={organizationId}
                          state={erasure} onDone={load} />
          )}
        </>
      )}
    </div>
  );
}
