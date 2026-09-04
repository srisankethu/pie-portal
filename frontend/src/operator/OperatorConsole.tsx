/** PIE operating the platform: the five command-line tools, as a screen.
 *
 * `docs/operator-console.md` is the design. Three things about this file follow
 * from it and are worth stating where somebody editing it will read them:
 *
 * **It computes nothing.** Every number here was answered by
 * `routers/operator`, which in turn calls the modules the CLIs call. A console
 * that derived a plan, a count or a queue order of its own would be a second
 * answer to a question an operator also asks on the command line, and the two
 * would drift — which is the responsibility duplication CLAUDE.md §2 names.
 *
 * **The tenant panel is a door, not a view.** Reading inside a customer's book
 * needs a break-glass grant with a reason the customer reads verbatim. The UI
 * does not hide that behind a spinner: opening one is a deliberate act with a
 * form, and the 403 that comes back without one is shown as what it is.
 *
 * **Nothing here shows money.** Not because the screen is careful, but because
 * the endpoints do not carry it: `/organizations` is name, plan, currency and
 * a date. If a figure from inside a customer's book ever appears in this file,
 * something upstream started serving it and that is the bug.
 */
import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import { formatDateTime } from "../when";
import { DataGrid, type ColDef } from "../platform/DataGrid";
import { EmptyState, ErrorState, SectionHeader, StatusChip } from "../platform/kit";
import * as api from "./api";
import type {
  AccessView, Enquiry, OrgRow, Requests, SupportView, Whoami,
} from "./api";

/** The plans an operator may put a tenant on.
 *
 *  Written out rather than fetched: the server validates against `PlanTier` and
 *  refuses anything else, so this list being stale shows up as a refusal rather
 *  than as a wrong grant. It is the same three the landing page names.
 */
const PLANS = ["free", "intelligence", "platform"] as const;

/** A call's state, kept per panel rather than globally.
 *
 *  `error` holds the server's own sentence — see `api.call`. A console that
 *  replaced "no active break-glass grant for org_x" with "Something went
 *  wrong" would be hiding the one message that says what to do next. */
type Load<T> = { status: "loading" } | { status: "ready"; data: T }
             | { status: "failed"; error: string };

function useLoad<T>(fetcher: () => Promise<T>, deps: unknown[] = []) {
  const [state, setState] = useState<Load<T>>({ status: "loading" });
  const reload = useCallback(() => {
    setState({ status: "loading" });
    fetcher()
      .then((data) => setState({ status: "ready", data }))
      .catch((e) => setState({ status: "failed", error: String(e?.message ?? e) }));
    // The fetcher is rebuilt per render by every caller below, so it cannot be
    // a dependency without looping. The deps the caller names are the real ones.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(reload, [reload]);
  return { state, reload };
}

function Loaded<T>({ load, children }: {
  load: Load<T>; children: (data: T) => ReactNode;
}) {
  if (load.status === "loading") return <Typography color="text.secondary">Reading…</Typography>;
  if (load.status === "failed") return <ErrorState error={load.error} />;
  return <>{children(load.data)}</>;
}

// ── the door ────────────────────────────────────────────────────────────────
function SignIn({ onSignedIn }: { onSignedIn: (who: Whoami) => void }) {
  const [key, setKey] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    api.rememberKey(key.trim());
    try {
      // `whoami` rather than trusting a 200 from the first panel: a bad key
      // must be reported at the door, not as an empty queue that looks like
      // good news. This is the same rule §1 states about absent evidence.
      onSignedIn(await api.get<Whoami>("/whoami"));
    } catch (err) {
      api.forgetKey();
      setError(err instanceof api.OperatorError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Box sx={{ maxWidth: 460, mx: "auto", mt: 10, px: 2 }}>
      <Paper sx={{ p: 3 }}>
        <form onSubmit={submit}>
          <Typography variant="h6" gutterBottom>PIE operator console</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            This is not a customer login. Mint a key with{" "}
            <code>python -m app.operator mint &lt;your name&gt;</code> on the
            box; it is shown once.
          </Typography>
          <TextField
            label="Operator key" fullWidth autoFocus type="password"
            value={key} onChange={(e) => setKey(e.target.value)}
            disabled={busy} placeholder="pieop_…" sx={{ mb: 2 }} />
          {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
          <Button type="submit" variant="contained" disabled={busy || !key.trim()}>
            {busy ? "Checking…" : "Open the console"}
          </Button>
        </form>
      </Paper>
    </Box>
  );
}

// ── the three ask-queues ────────────────────────────────────────────────────
function Queues({ onDecided }: { onDecided: () => void }) {
  const { state, reload } = useLoad<Requests>(() => api.get<Requests>("/requests"));
  const [busy, setBusy] = useState("");

  const decide = async (requestId: string, apply: boolean) => {
    setBusy(requestId);
    try {
      await api.post(`/requests/${requestId}/decide`, { apply });
      reload();
      onDecided();
    } finally {
      setBusy("");
    }
  };

  return (
    <Loaded load={state}>
      {(data) => {
        const nothing = !data.at_signup.length && !data.in_product.length
                        && !data.enquiries.length;
        if (nothing) {
          return <EmptyState title="Nobody is asking for anything"
                             reason="No sign-up asks, no plan requests, no unanswered enquiries." />;
        }
        return (
          <Stack spacing={3}>
            {data.in_product.length > 0 && (
              <Paper sx={{ p: 2 }}>
                <SectionHeader level="section"
                               title="Asked from inside the product"
                               sub="The only queue here that carries a decision." />
                <Stack spacing={1} sx={{ mt: 1 }}>
                  {data.in_product.map((r) => (
                    <Stack key={r.request_id} direction="row" spacing={2}
                           sx={{ alignItems: "center", flexWrap: "wrap", rowGap: 1 }}>
                      <Typography sx={{ minWidth: 180 }}>{r.organization_id}</Typography>
                      <Chip size="small" label={`${r.from} → ${r.to}`} />
                      <Typography variant="body2" color="text.secondary">
                        {r.requested_by} · {formatDateTime(r.requested_at)}
                      </Typography>
                      {r.note && <Typography variant="body2">{r.note}</Typography>}
                      <Box sx={{ flex: 1 }} />
                      <Button size="small" variant="contained"
                              disabled={busy === r.request_id}
                              onClick={() => decide(r.request_id, true)}>Grant</Button>
                      <Button size="small" disabled={busy === r.request_id}
                              onClick={() => decide(r.request_id, false)}>Decline</Button>
                    </Stack>
                  ))}
                </Stack>
              </Paper>
            )}
            {data.at_signup.length > 0 && (
              <Paper sx={{ p: 2 }}>
                <SectionHeader
                  level="section"
                  title="Wanted at sign-up"
                  sub="What a company said it wanted on the form. No decision attached — grant it on the Tenants tab." />
                <Stack spacing={1} sx={{ mt: 1 }}>
                  {data.at_signup.map((r) => (
                    <Stack key={r.organization_id} direction="row" spacing={2}
                           sx={{ alignItems: "center" }}>
                      <Typography sx={{ minWidth: 180 }}>{r.name}</Typography>
                      <Chip size="small" label={`on ${r.on}`} />
                      <Chip size="small" color="primary" label={`wants ${r.wants}`} />
                    </Stack>
                  ))}
                </Stack>
              </Paper>
            )}
            {data.enquiries.length > 0 && (
              <Paper sx={{ p: 2 }}>
                <SectionHeader level="section"
                               title="Asked from the public site"
                               sub={`${data.enquiries.length} waiting. The Enquiries tab is where they get answered.`} />
              </Paper>
            )}
          </Stack>
        );
      }}
    </Loaded>
  );
}

// ── the demo-request queue ──────────────────────────────────────────────────
function Enquiries() {
  const [handled, setHandled] = useState(false);
  const { state, reload } = useLoad<{ enquiries: Enquiry[] }>(
    () => api.get(`/enquiries${handled ? "?handled=true" : ""}`), [handled]);

  const answer = async (row: Enquiry) => {
    await api.post(`/enquiries/${row.id}/handled`);
    reload();
  };

  const columns: ColDef<Enquiry>[] = [
    { field: "created_at", headerName: "Arrived", width: 150,
      valueFormatter: (p) => formatDateTime(p.value) },
    { field: "company", headerName: "Company", flex: 1 },
    { field: "name", headerName: "Who", width: 160 },
    { field: "email", headerName: "Email", width: 220 },
    { field: "phone", headerName: "Phone", width: 140 },
    { field: "erp", headerName: "Runs", width: 160 },
    { field: "plan", headerName: "Asked about", width: 140 },
    { field: "message", headerName: "Said", flex: 2 },
    ...(handled
      ? [{ field: "handled_by", headerName: "Answered by", width: 140 } as ColDef<Enquiry>]
      : [{
          headerName: "", width: 130, sortable: false, filter: false,
          cellRenderer: (p: { data: Enquiry }) => (
            <Button size="small" onClick={() => answer(p.data)}>Mark answered</Button>
          ),
        } as ColDef<Enquiry>]),
  ];

  return (
    <Stack spacing={2}>
      <Tabs value={handled ? 1 : 0} onChange={(_, v) => setHandled(v === 1)}>
        <Tab label="Waiting" />
        <Tab label="Answered" />
      </Tabs>
      <Loaded load={state}>
        {(data) => (
          <DataGrid<Enquiry>
            rows={data.enquiries} columns={columns} twoLineRows
            getRowId={(r) => r.id}
            ariaLabel={handled ? "Answered enquiries" : "Enquiries waiting for a reply"}
            empty={<EmptyState
              title={handled ? "Nothing answered yet" : "Nobody is waiting for a reply"}
              reason={handled
                ? "Enquiries appear here once somebody marks them answered."
                : "Every enquiry from the site's demo-request form lands here."} />}
          />
        )}
      </Loaded>
    </Stack>
  );
}

// ── the tenants ─────────────────────────────────────────────────────────────
function TenantPanel({ org, onClose }: { org: OrgRow; onClose: () => void }) {
  const { state, reload } = useLoad<AccessView>(
    () => api.get(`/organizations/${org.organization_id}/access`),
    [org.organization_id]);
  const [why, setWhy] = useState("");
  const [support, setSupport] = useState<Load<SupportView> | null>(null);
  const [plan, setPlan] = useState(org.plan);
  const [note, setNote] = useState("");

  const open = async () => {
    setNote("");
    try {
      await api.post(`/organizations/${org.organization_id}/access`,
                     { justification: why });
      setWhy("");
      reload();
    } catch (e) {
      setNote(String((e as Error).message));
    }
  };

  const look = async () => {
    setSupport({ status: "loading" });
    try {
      setSupport({ status: "ready",
                   data: await api.get(`/organizations/${org.organization_id}/support`) });
    } catch (e) {
      setSupport({ status: "failed", error: String((e as Error).message) });
    }
    // Reload the access view afterwards, so the operator sees their own reach
    // in the log below. Without this the panel shows the grant and not the
    // ACCESSED event it just wrote — which reads as "opening the door is
    // recorded, using it is not", the exact opposite of what `trust/access`
    // does and the property it exists for. Outside the try: the reach is
    // logged whether or not the read succeeded, so the log is worth
    // refreshing either way.
    reload();
  };

  const grant = async () => {
    setNote("");
    try {
      await api.post(`/organizations/${org.organization_id}/plan`, { plan });
      setNote(`On ${plan}.`);
    } catch (e) {
      setNote(String((e as Error).message));
    }
  };

  return (
    <Dialog open onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>{org.name}</DialogTitle>
      <DialogContent dividers>
        <Stack spacing={3}>
          <Box>
            <SectionHeader level="widget" title="Plan"
                           sub="Granting here is the same function the CLI calls." />
            <Stack direction="row" spacing={2} sx={{ mt: 1, alignItems: "center" }}>
              <TextField select size="small" label="Plan" value={plan}
                         onChange={(e) => setPlan(e.target.value)} sx={{ minWidth: 200 }}>
                {PLANS.map((p) => <MenuItem key={p} value={p}>{p}</MenuItem>)}
              </TextField>
              <Button variant="contained" onClick={grant}
                      disabled={plan === org.plan}>Put them on it</Button>
              {org.wants && <Chip size="small" color="primary" label={`asked for ${org.wants}`} />}
            </Stack>
            {note && <Alert severity="info" sx={{ mt: 2 }}>{note}</Alert>}
          </Box>

          <Box>
            <SectionHeader
              level="widget"
              title="Break-glass"
              sub="Reading anything inside their book needs a grant, and the reason you type is shown to them verbatim." />
            <Loaded load={state}>
              {(data) => (
                <Stack spacing={2} sx={{ mt: 1 }}>
                  {data.active_grant ? (
                    <Alert severity="warning"
                           action={<Button size="small" onClick={async () => {
                             await api.del(`/access/${data.active_grant!.grant_id}`);
                             setSupport(null);
                             reload();
                           }}>Hand it back</Button>}>
                      Open until {formatDateTime(data.active_grant.expires_at)} —
                      “{data.active_grant.justification}”
                    </Alert>
                  ) : (
                    <Stack direction="row" spacing={2} sx={{ alignItems: "flex-start" }}>
                      <TextField
                        size="small" fullWidth label="Why you need to look"
                        placeholder="PIE-114 — they say their sync is stuck"
                        value={why} onChange={(e) => setWhy(e.target.value)}
                        helperText="At least 10 characters. The customer reads this." />
                      <Button variant="outlined" onClick={open}
                              disabled={why.trim().length < 10}>Open access</Button>
                    </Stack>
                  )}

                  <Stack direction="row" spacing={2} sx={{ alignItems: "center" }}>
                    <Button size="small" onClick={look}
                            disabled={!data.active_grant}>Read their status</Button>
                    {!data.active_grant && (
                      <Typography variant="body2" color="text.secondary">
                        Refused without a grant — not hidden, refused.
                      </Typography>
                    )}
                  </Stack>

                  {support?.status === "failed" &&
                    <Alert severity="error">{support.error}</Alert>}
                  {support?.status === "ready" && (
                    <Paper variant="outlined" sx={{ p: 2 }}>
                      <Typography variant="body2">
                        {support.data.people.active} active of {support.data.people.total} people.
                      </Typography>
                      <Typography variant="body2">
                        {support.data.last_sync
                          ? `Last sync ${support.data.last_sync.status} at ${formatDateTime(support.data.last_sync.started_at)}.`
                          : "This book has never synced."}
                      </Typography>
                    </Paper>
                  )}

                  <Box>
                    <Typography variant="subtitle2" gutterBottom>
                      What staff have done here
                    </Typography>
                    {data.events.length === 0
                      ? <Typography variant="body2" color="text.secondary">
                          Nobody has ever reached into this tenant.
                        </Typography>
                      : <Stack spacing={0.5}>
                          {data.events.map((e, i) => (
                            <Typography key={i} variant="body2" color="text.secondary">
                              {formatDateTime(e.at)} · {e.action} · {e.staff} · {e.detail}
                            </Typography>
                          ))}
                        </Stack>}
                  </Box>
                </Stack>
              )}
            </Loaded>
          </Box>
        </Stack>
      </DialogContent>
      <DialogActions><Button onClick={onClose}>Close</Button></DialogActions>
    </Dialog>
  );
}

function Tenants({ reloadToken }: { reloadToken: number }) {
  const { state } = useLoad<{ organizations: OrgRow[] }>(
    () => api.get("/organizations"), [reloadToken]);
  const [open, setOpen] = useState<OrgRow | null>(null);

  const columns: ColDef<OrgRow>[] = [
    { field: "name", headerName: "Company", flex: 1 },
    { field: "organization_id", headerName: "Id", width: 180 },
    { field: "plan", headerName: "Licensed on", width: 150,
      cellRenderer: (p: { value: string }) => <StatusChip label={p.value} tone="neutral" /> },
    { field: "wants", headerName: "Asked for", width: 150 },
    { field: "currency", headerName: "Currency", width: 110 },
    { field: "created_at", headerName: "Since", width: 150,
      valueFormatter: (p) => formatDateTime(p.value) },
  ];

  return (
    <>
      <Loaded load={state}>
        {(data) => (
          <DataGrid<OrgRow>
            rows={data.organizations} columns={columns}
            getRowId={(r) => r.organization_id}
            onRowClick={(r) => setOpen(r)}
            ariaLabel="Tenants"
            empty={<EmptyState title="No tenants yet"
                               reason="Provision one with `python -m app.provision_org`." />} />
        )}
      </Loaded>
      {open && <TenantPanel org={open} onClose={() => setOpen(null)} />}
    </>
  );
}

// ── the shell ───────────────────────────────────────────────────────────────
export function OperatorConsole() {
  const [who, setWho] = useState<Whoami | null>(null);
  const [checking, setChecking] = useState(api.storedKey() !== "");
  const [tab, setTab] = useState(0);
  // Bumped when something one panel did changes what another shows — granting a
  // plan from the queue changes the tenant list. One counter rather than a
  // shared cache: the console has three panels, and a query client would be a
  // larger mechanism than the problem.
  const [token, setToken] = useState(0);

  // A key already in `sessionStorage` (a reload) is re-checked rather than
  // trusted: it may have been revoked since the tab was opened.
  useEffect(() => {
    if (!checking) return;
    api.get<Whoami>("/whoami")
      .then(setWho)
      .catch(() => api.forgetKey())
      .finally(() => setChecking(false));
  }, [checking]);

  if (checking) return null;
  if (!who) return <SignIn onSignedIn={setWho} />;

  return (
    <Box sx={{ maxWidth: 1400, mx: "auto", p: 3 }}>
      <Stack direction="row" spacing={2} sx={{ mb: 2, alignItems: "center" }}>
        <Typography variant="h5" sx={{ flex: 1 }}>PIE operator console</Typography>
        <Chip label={who.operator_id} />
        <Button size="small" onClick={() => { api.forgetKey(); setWho(null); }}>
          Sign out
        </Button>
      </Stack>
      <Alert severity="info" sx={{ mb: 2 }}>
        This console is PIE's, not a customer's. Nothing here reads inside a
        book without a break-glass grant, and every grant is shown to the
        customer with the reason you typed.
      </Alert>
      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}>
        <Tab label="Queues" />
        <Tab label="Enquiries" />
        <Tab label="Tenants" />
      </Tabs>
      {tab === 0 && <Queues onDecided={() => setToken((t) => t + 1)} />}
      {tab === 1 && <Enquiries />}
      {tab === 2 && <Tenants reloadToken={token} />}
    </Box>
  );
}
