import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Checkbox from "@mui/material/Checkbox";
import Divider from "@mui/material/Divider";
import FormControlLabel from "@mui/material/FormControlLabel";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import { DataGrid, numeric } from "./DataGrid";
import { count, money } from "../money";
import { formatDateTime, since as when, todayISO } from "../when";
import { papi } from "./api";
import {
  ErrorState, FactTable, LoadingState, Meta, PanelMark, SectionHeader,
  StatusChip, TOUCH, type Tone,
} from "./kit";
import { ConnectionsPanel } from "./ConnectionsPanel";
import { RunLogPanel } from "./RunLogPanel";
import { SkippedRowsPanel } from "./SkippedRowsPanel";
import { SyncStatusCard, useSync } from "./SyncStatus";
import type {
  DataStatus, PlatformSession, SyncRun, UnresolvedReference,
} from "./types";
import { Bp, Labelled, Tip } from "./ui";

/**
 * Data & connection.
 *
 * Answers one question without requiring a terminal: is this showing the
 * company's real books, or sample data? The state is stated in words rather
 * than left to be inferred from whether numbers look plausible — a demo
 * dataset and a live one are indistinguishable by eye, which is exactly how
 * someone ends up trusting a decision built on fixtures.
 *
 * The companies themselves live in ``ConnectionsPanel``: an organization can
 * read as many Zoho books as the business keeps, and per-company health does
 * not fit in a single status badge.
 *
 * **Nothing here may answer with a benign default.** The screen is read to find
 * out what is and is not known, so every branch that cannot state something
 * says so: a status request that failed does not fall through to "No sync has
 * run yet" (it did not ask), a read model that reported no counts says the
 * counts are missing rather than drawing an empty table, and an outcome is a
 * `StatusChip` rather than one more row in a column of counters — §6, and
 * because "Failed" set in the same ink as "Customers" is scrolled past.
 */

const RESULT_UI: Record<string, string> = {
  OK: "Succeeded",
  PARTIAL: "Stopped part-way",
  FAILED: "Failed",
  RUNNING: "Still running",
  QUEUED: "Queued",
};

/** The outcome as a tone. PARTIAL is amber and FAILED red because they are
 *  different facts: a run that stopped part-way wrote rows and kept them. */
const RESULT_TONE: Record<string, Tone> = {
  OK: "good", PARTIAL: "warn", FAILED: "bad",
  RUNNING: "info", QUEUED: "info", IDLE: "neutral",
};

/** A default worth offering rather than a default worth hiding: eighteen months
 *  gives the detectors a full recent window, a full comparison window and room
 *  above the six-month history floor. The operator can move it either way.
 *
 *  Calendar arithmetic on the business day's own `YYYY-MM-DD`, never through a
 *  `Date`. The previous version built one from `todayISO()` — which parses as
 *  *browser-local* midnight — then read it back out with
 *  `toLocaleDateString("en-CA")`, which is the browser's zone again and, as
 *  `when.ts` says of the same call it removed from `todayISO`, produces
 *  `YYYY-MM-DD` by a coincidence of that locale's conventions rather than by
 *  asking for it. Two zone assumptions that happen to cancel are still two
 *  assumptions, and `when.ts` exists so that a screen carries none.
 *
 *  It also lost a whole month on the 29th, 30th and 31st: `setMonth(m - 18)`
 *  from 31 August overflows February into 3 March, and the `setDate(1)` meant
 *  to floor it then landed on 1 March rather than 1 February. Months are the
 *  only unit here, so months are the only unit it counts in.
 *
 *  This is `routers/connections._default_since`'s arithmetic exactly — the
 *  same total-months division, against the same 18, which is the server's
 *  `DEFAULT_HISTORY_MONTHS`. That the figure is written twice is a real
 *  duplication and it is not closable from here: `DataStatus` carries
 *  `auto_sync.covers_from` (which this field prefers the moment it arrives) but
 *  not the server's own default, so a client with no coverage yet has nothing
 *  to read. Until it does, the two at least compute it the same way. */
function defaultSince(): string {
  const [y, m] = todayISO().split("-").map(Number);
  const months = y * 12 + (m - 1) - 18;
  const year = Math.floor(months / 12);
  const month = months % 12 + 1;
  return `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}-01`;
}

/** One row of a fact panel: a label, the figure, and — where the label is
 *  accurate but incomplete — the tooltip that says what the figure means. */
type FactRow = { label: string; value: ReactNode; tip?: string; note?: ReactNode };

/** A group's rows in the shape `kit.FactTable` takes: label, value, note.
 *
 *  The panel is three `FactTable`s rather than one, because the kit's fact
 *  panel has no row groups and the grouping is what makes this column of
 *  counters readable — one undifferentiated list is a list nobody reaches the
 *  bottom of, and the bottom is where the platform says what it *made* of the
 *  pull. Each group's name is the table's own `<caption>`, so it is announced
 *  with the rows it heads rather than merely sitting above them, and it keeps
 *  the mark rung it had through `PanelMark`. `.facttable` is `width: 100%` and
 *  its values are right-aligned, so three tables line up on both edges. */
function factRows(rows: FactRow[]): readonly (readonly [ReactNode, ReactNode, ReactNode?])[] {
  return rows.map((r): readonly [ReactNode, ReactNode, ReactNode?] => [
    r.tip ? <Labelled tip={r.tip}>{r.label}</Labelled> : r.label,
    r.value,
    r.note,
  ]);
}

/** What the last run did: when it ran, what it read, what came of it.
 *
 *  Every figure is the run's own count, rendered — none is computed here.
 *  Through `count()` rather than as a bare number, so four hundred thousand
 *  sales lines group the way this organization's own figures do everywhere
 *  else. The outcome is not in this list; it is the chip on the panel. */
function factGroups(s: SyncRun): { title: string; rows: FactRow[] }[] {
  return [
    {
      title: "This run",
      rows: [
        {
          label: "Started", value: when(s.started_at),
          // The absolute time under the relative one. "3 days ago" is the
          // answer somebody wants; the timestamp is what they quote when
          // asking somebody else about the same run.
          note: s.started_at ? formatDateTime(s.started_at) : undefined,
        },
        {
          label: "Read from", value: s.since || "rolling window",
          note: "the start date this run was given",
        },
      ],
    },
    {
      title: "What it read",
      rows: [
        { label: "Customers", value: count(s.customers) },
        { label: "Products", value: count(s.products) },
        { label: "Sales lines", value: count(s.sales_txns) },
        {
          label: "Cost records", value: count(s.cost_records),
          tip: "Bill lines — what the stock cost. Without these there is no margin anywhere in the platform, only revenue. A zero here almost always means the ZohoBooks.bills.READ scope was not granted.",
        },
        // The supply stage. Listed unconditionally, including at zero:
        // "Suppliers 0" with a reason attached is the answer to "why are
        // vendors not being read", and an absent row is not.
        {
          label: "Suppliers", value: count(s.vendors),
          tip: "Suppliers, from the same Zoho contact list as customers. Needs no scope beyond ZohoBooks.contacts.READ, so a zero here means the stage did not run rather than that it was refused.",
        },
        {
          label: "Payments", value: count(s.payments),
          tip: "Money in, with the invoices each receipt settled. Needs ZohoBooks.customerpayments.READ. Without it every invoice looks paid the day it was raised, and the Cash screen is empty.",
        },
        {
          label: "Purchase orders", value: count(s.purchase_orders),
          tip: "What is on the way from suppliers. Needs ZohoBooks.purchaseorders.READ.",
        },
        {
          label: "Sales orders", value: count(s.sales_orders),
          tip: "Orders customers have placed but that have not been invoiced yet — demand already promised. Needs ZohoBooks.salesorders.READ.",
        },
        {
          label: "Payments out", value: count(s.vendor_payments),
          tip: "Money out, to suppliers. Needs ZohoBooks.vendorpayments.READ. Payments in alone are revenue collected, not cash — both sides are needed before liquidity means anything.",
        },
        {
          label: "Stock snapshots", value: count(s.stock_snapshots),
          note: "read from the item list, at no extra cost",
        },
        {
          label: "Documents read", value: count(s.documents_fetched),
          note: s.documents_resumed
            ? `${count(s.documents_resumed)} were already held and were not read again`
            : "invoices and bills fetched individually",
        },
      ],
    },
    {
      title: "What came of it",
      rows: [
        {
          label: "Accounts assigned", value: count(s.assignments),
          tip: "Maps a Zoho salesperson to a platform account. Needs the ZohoBooks.users.READ scope — without it accounts stay unassigned and every decision routes to management.",
          note: "from Zoho's salesperson on the latest invoice",
        },
        { label: "Signals detected", value: count(s.signals_emitted) },
        { label: "Decisions created", value: count(s.decisions_created) },
        {
          label: "Rows skipped", value: count(s.skipped_count),
          tip: "A row Zoho returned that could not be used — a line with no item, a document in a currency this organization does not trade in. Recorded with a reason so the gap is explainable, never dropped silently.",
        },
      ],
    },
  ];
}

export function DataScreen({ session, onSynced }: { session: PlatformSession; onSynced: () => void }) {
  const [status, setStatus] = useState<DataStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [since, setSince] = useState<string>(defaultSince());
  // Whether the operator has chosen a date themselves. Until they do, the
  // field follows the organization's existing coverage once the status
  // arrives — "sync again" should mean "the window I already have", not
  // "eighteen months because that is the constant in the code".
  const [sinceTouched, setSinceTouched] = useState(false);
  const [full, setFull] = useState(false);

  const load = useCallback(async () => {
    setChecking(true);
    setError(null);
    try {
      setStatus(await papi.dataStatus(session.token));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setChecking(false);
    }
  }, [session.token]);

  useEffect(() => {
    load();
  }, [load]);

  const coveredFrom = status?.auto_sync?.covers_from ?? null;
  useEffect(() => {
    if (coveredFrom && !sinceTouched) setSince(coveredFrom);
  }, [coveredFrom, sinceTouched]);

  // Starting a sync and watching it are one concern, in one place — see
  // SyncStatus.tsx. The screen renders from that state rather than from the
  // response to whatever request it last made, which is what lets a page
  // opened mid-pull show the pull.
  const sync = useSync(session.token, useCallback(() => {
    // A job just finished: the read-model counts on this page are now stale.
    load();
    onSynced();
  }, [load, onSynced]));

  /** Queue a pull. `connectionId` names one company; omitted means every
   *  enabled one. Returns immediately — the card takes it from there. */
  async function startSync(connectionId?: string, fromDate?: string, reread?: boolean) {
    await sync.start({
      since: (fromDate ?? since) || undefined,
      full: reread ?? full,
      connection_id: connectionId,
    });
    setFull(false);
  }

  const s = status?.last_sync;
  const canSync = Boolean(status?.can_sync);
  // Which companies are pulling *right now*, by connection. This used to be one
  // organization-wide flag, and that flag disabled all three companies' buttons
  // the moment any one of them started — so the per-connection concurrency the
  // server grew was unreachable from the screen that would have used it.
  const busyConnections = (sync.state?.busy_connections ?? []) as string[];
  // The "every company" button is a different question and keeps the old
  // answer: it is one pull across all of them, and two of those really would
  // read the same books twice.
  const allBusy = !(sync.state?.can_start ?? true) || sync.busy;
  const readModel = Object.entries(status?.read_model || {});

  return (
    <Box>
      <SectionHeader
        title="Data & connection"
        sub="Where the numbers come from, and when they last arrived."
      />

      {error && (
        <Box sx={{ mb: 2 }}>
          {/* `onRetry`, because without it this error came with no way to act
              on it. The "Refresh status" button that would otherwise re-fetch
              sits behind `canSync`, which is read off `status` — the very call
              that just failed — so on a first-load failure it is hidden from
              everyone, managers and owners included, not only from the
              salesperson who never had it. */}
          <ErrorState title="Could not read the connection status" error={error}
                      onRetry={load} busy={checking} />
        </Box>
      )}

      <ConnectionsPanel
        session={session}
        canSync={canSync}
        onSync={(id, from, reread) => startSync(id, from, reread)}
        // An all-companies run reads *this* company too, so its own button is
        // busy for the duration. It reports itself with a NULL connection, so
        // it never appears in `busy_connections` — without this, every card
        // offered a pull the server would decline by handing back the umbrella
        // job, which looks like a button that does nothing.
        busyConnections={busyConnections}
        everyCompanyBusy={Boolean(sync.state?.active
                                  && sync.state.active.connection_id == null)}
        starting={sync.busy}
      />

      {/* ── what to pull ── */}
      <Bp sx={{ p: 2.5, mt: 2, mb: 2 }}>
        <SectionHeader
          level="widget"
          title="Pull the books"
          tip="One pull reads invoices and bills, rebuilds the Customer × Item metrics from what landed, then runs the detectors. Nothing here is generated — every figure comes from a document Zoho returned."
        />
        {!canSync ? (
          <Typography variant="body2" color="text.secondary">
            Syncing is a manager or owner action.
          </Typography>
        ) : (
          <>
            <Stack spacing={1.5} sx={{ maxWidth: 560, alignItems: "flex-start" }}>
              <Stack direction="row" spacing={0.5} sx={{ alignItems: "center" }}>
                {/* The label rides inside the field rather than above it, so
                    the explanation can sit beside the control as a real button
                    instead of as a `<button>` nested inside a `<label>`. */}
                <TextField
                  id="sync-since"
                  type="date"
                  label="Read the books from"
                  value={since}
                  onChange={(e) => { setSinceTouched(true); setSince(e.target.value); }}
                  slotProps={{
                    inputLabel: { shrink: true },
                    htmlInput: { max: todayISO() },
                  }}
                  sx={{ width: 200 }}
                />
                <Tip text="Every invoice and bill after this date is fetched individually, so an earlier date means a longer pull. The detectors compare the last 90 days against the 90 before that and need six months of history before they will call a decline — eighteen months covers all of it with room to spare." />
              </Stack>

              <Stack direction="row" spacing={0.5} sx={{ alignItems: "center" }}>
                <FormControlLabel
                  sx={{ ...TOUCH, mr: 0 }}
                  control={<Checkbox size="small" checked={full}
                                     onChange={(e) => setFull(e.target.checked)} />}
                  label={
                    <Typography variant="body2">
                      Re-read everything, including documents already held
                    </Typography>
                  }
                />
                <Tip text="Normally a pull skips documents it already holds, which is what makes a repeat run fast. Tick this after granting a scope that was missing — the rows are there, but the fields that scope unlocks are not." />
              </Stack>
            </Stack>

            <Stack direction="row" spacing={1} useFlexGap
                   sx={{ mt: 2, flexWrap: "wrap", alignItems: "center" }}>
              <Button variant="outlined" size="small" onClick={load} disabled={checking}>
                {checking ? "Checking…" : "Refresh status"}
              </Button>
              <Button
                variant="contained" size="small"
                onClick={() => startSync()}
                disabled={allBusy}
              >
                {sync.state?.active ? "Sync running…" : sync.busy ? "Starting…" : "Sync every company"}
              </Button>
              <Tip text="Runs one pull per enabled company, in turn. To pull just one, use the button on its card above." />
              {sync.state?.active && (
                <Typography variant="body2" color="text.secondary">
                  A sync is already running — starting another would pull the same books twice.
                </Typography>
              )}
            </Stack>

            {/* ── the automatic pull ── */}
            {status?.auto_sync && (
              <>
                <Divider sx={{ my: 2 }} />
                <Stack direction="row" spacing={1.5} useFlexGap
                       sx={{ flexWrap: "wrap", alignItems: "center" }}>
                  {!status.auto_sync.available ? (
                    <>
                      <Typography variant="overline" color="text.secondary">
                        Automatic sync
                      </Typography>
                      <Typography variant="body2" color="text.secondary">
                        Applies to a live Zoho connection — this deployment is
                        showing sample data.
                      </Typography>
                    </>
                  ) : (
                    <>
                      <TextField
                        id="auto-sync"
                        select
                        label="Automatic sync"
                        value={String(status.auto_sync.hours)}
                        onChange={async (e) => {
                          const hours = Number(e.target.value);
                          try {
                            const r = await papi.setAutoSync(session.token, hours);
                            setStatus((prev) => (prev ? { ...prev, auto_sync: r.auto_sync } : prev));
                          } catch (err) {
                            setError((err as Error).message);
                          }
                        }}
                        sx={{ width: 200 }}
                      >
                        <MenuItem value="0">Off</MenuItem>
                        {[1, 3, 6, 12, 24].map((h) => (
                          <MenuItem key={h} value={String(h)}>
                            Every {h === 1 ? "hour" : `${h} hours`}
                          </MenuItem>
                        ))}
                      </TextField>
                      <Tip text="Each automatic pull re-reads the organization's existing window — documents already held and unchanged cost nothing, and a bill entered today but dated last week is still caught, which a pull starting 'from the last sync' would silently miss." />
                      <Typography variant="body2" color="text.secondary">
                        {status.auto_sync.hours === 0
                          ? "Off — the books refresh only when somebody syncs."
                          : `Next around ${formatDateTime(status.auto_sync.next_run_at)}` +
                            (status.auto_sync.covers_from
                              ? `, re-reading from ${status.auto_sync.covers_from}.`
                              : ".")}
                      </Typography>
                    </>
                  )}
                </Stack>
              </>
            )}
          </>
        )}
      </Bp>

      {/* ── the job itself: state first, never the last response ── */}
      <SectionHeader
        level="section"
        title="Sync status"
        sub="What a pull is doing now, what the last one landed, and what it could not use."
      />
      <SyncStatusCard sync={sync} canSync={canSync} onRetry={() => startSync()} />

      {/* Rotation used to be a panel of its own here. It has moved onto the
          connection card above — a revocable refresh token is a Zoho
          mechanism, not a concept every connector will have, and the control
          belongs on the thing somebody has just decided to rotate rather than
          three sections further down the page. */}

      {!status ? (
        error ? (
          /* Not an empty state, and not silence either. This branch used to
             fall through to the panels below with no status at all, so a
             failed request drew "No sync has run yet" — a claim about the
             books made by a screen that had not managed to ask. */
          <Alert severity="warning" sx={{ mt: 2 }}>
            <AlertTitle>The last run and the read model are not known</AlertTitle>
            The status request did not answer, so what the last sync landed and
            what is held now cannot be stated either way. The error above says
            what went wrong and offers the retry.
          </Alert>
        ) : (
          <Box sx={{ mt: 2 }}>
            <LoadingState rows={1} height={90} label="Reading the sync status…" />
          </Box>
        )
      ) : (
        <>
          <Box sx={{
            mt: 2, display: "grid", gap: 2, alignItems: "start",
            gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" },
          }}>
            <Bp sx={{ p: 2 }}>
              <SectionHeader
                level="widget"
                title="Last sync"
                actions={s ? (
                  <>
                    <StatusChip
                      label={RESULT_UI[s.status] || s.status}
                      tone={RESULT_TONE[s.status] ?? "neutral"}
                      tip="How the last run ended. A run that stopped part-way kept the rows it had written — running it again carries on from there."
                    />
                    {/* The source belongs on the block somebody screenshots.
                        The card above says it too, for the run it is watching;
                        this panel is a different block of figures and the
                        counters under it are fixture rows. A chip rather than a
                        second copy of that sentence. */}
                    {s.source === "fixture" && (
                      <StatusChip
                        label="Sample source"
                        tone="warn"
                        tip="This run read the offline sample source, not your books. Every figure below is a fixture row."
                      />
                    )}
                  </>
                ) : undefined}
              />
              {!s ? (
                <Typography variant="body2" color="text.secondary">
                  No sync has run yet.{" "}
                  {canSync
                    ? "Press Sync every company to pull."
                    : "A manager or owner can start one."}
                </Typography>
              ) : (
                <>
                  {s.error && (
                    /* Above the counters, not below them: the reason a run
                       stopped is what the panel is opened for, and a column of
                       figures is a long way to scroll past it.
                       Warning, not error, when the run was PARTIAL: rows were
                       kept and the next run continues from here, so the
                       severity that says "nothing survived" would overstate
                       it. */
                    <Alert
                      severity={s.status === "PARTIAL" ? "warning" : "error"}
                      sx={{ mb: 2 }}
                    >
                      <AlertTitle>
                        {s.status === "PARTIAL" ? "Why it stopped" : "Why it failed"}
                      </AlertTitle>
                      {s.error}
                      {s.status === "PARTIAL" && (
                        <Box sx={{ mt: 1 }}>
                          The rows below were kept. Running the sync again continues
                          from here rather than starting over.
                        </Box>
                      )}
                    </Alert>
                  )}
                  {/* The air between groups, which the single table carried on
                      each group heading's own `pt`. A caption has only the
                      padding `FactTable` gives it, so the gap belongs between
                      the tables rather than inside one of them. */}
                  <Box sx={{ "& table + table": { mt: 1.5 } }}>
                    {factGroups(s).map((g) => (
                      <FactTable
                        key={g.title}
                        caption={<PanelMark>{g.title}</PanelMark>}
                        rows={factRows(g.rows)}
                      />
                    ))}
                  </Box>
                </>
              )}
            </Bp>

            <Bp sx={{ p: 2 }}>
              <SectionHeader
                level="widget"
                title="What is in the read model now"
                tip="Everything currently held for this organization, pooled across every connected company. These are the rows the analysis actually runs on."
              />
              {readModel.length === 0 ? (
                <Typography variant="body2" color="text.secondary">
                  The status response carried no read-model counts. That is not
                  the same as the read model being empty — nothing was said
                  either way.
                </Typography>
              ) : (
                <FactTable
                  rows={readModel.map(([k, v]): readonly [ReactNode, ReactNode] => [
                    k.replace(/_/g, " ").replace(/^\w/, (m) => m.toUpperCase()),
                    count(v),
                  ])}
                />
              )}
            </Bp>
          </Box>

          {s && (s.unresolved?.length ?? 0) > 0 && (
            <Box sx={{ mt: 3 }}>
              <SectionHeader
                level="widget"
                title="What could not be resolved"
                tip="Grouped by what is missing rather than by row: one discontinued item on four hundred bill lines is one thing to fix, and listing it four hundred times hides the other two."
                sub="One row per thing to fix, not one per row skipped."
              />
              <Bp sx={{ p: 0.25 }}>
                <DataGrid<UnresolvedReference>
                  ariaLabel="Unresolved references"
                  pageSize={10}
                  rows={s.unresolved}
                  columns={[
                    {
                      headerName: "What is missing", flex: 1.3, minWidth: 240,
                      filter: "agTextColumnFilter",
                      valueGetter: (p) =>
                        p.data?.label || p.data?.missing_id || p.data?.code || "",
                      cellRenderer: (p: { data?: UnresolvedReference }) => p.data ? (
                        <div>
                          <b>{p.data.label || p.data.missing_id || p.data.code}</b>
                          <Meta>
                            {p.data.sku ? `SKU ${p.data.sku} · ` : ""}
                            {p.data.kind.replace(/_/g, " ")} · {p.data.code}
                            {p.data.missing_id ? ` · id ${p.data.missing_id}` : ""}
                          </Meta>
                        </div>
                      ) : null,
                    },
                    numeric<UnresolvedReference>("lines", "Lines held up",
                                                 count, {
                      width: 145, flex: 0, sort: "desc",
                      headerTooltip: "How many document lines this one missing "
                        + "record is blocking. The list is ranked by it.",
                    }),
                    numeric<UnresolvedReference>("value", "Value", money,
                                                 { width: 140, flex: 0 }),
                    {
                      headerName: "Seen on", flex: 1, minWidth: 190,
                      sortable: false, filter: false, autoHeight: true,
                      cellRenderer: (p: { data?: UnresolvedReference }) => p.data ? (
                        <Box sx={{ py: 0.5 }}>
                          {p.data.examples.length === 0 ? (
                            <Meta>{p.data.first_seen || "—"}</Meta>
                          ) : p.data.examples.map((e, j) => (
                            <Meta key={j}>
                              {e.document}{e.date ? ` · ${e.date}` : ""}
                              {e.party ? ` · ${e.party}` : ""}
                            </Meta>
                          ))}
                          {p.data.lines > p.data.examples.length
                            && p.data.examples.length > 0 && (
                            <Meta>
                              and {count(p.data.lines - p.data.examples.length)} more
                            </Meta>
                          )}
                        </Box>
                      ) : null,
                    },
                    {
                      field: "fix", headerName: "What to do", flex: 1.6,
                      minWidth: 280, sortable: false, filter: false,
                      autoHeight: true, wrapText: true,
                    },
                  ]}
                />
              </Bp>
            </Box>
          )}

          {/* The rows themselves are kept below the worklist, not instead of
              it: the worklist is what to do, these are the evidence it was
              built from — and the sheet somebody reconciles against Zoho. */}
          {s && s.skipped_count > 0 && (
            <Box sx={{ mt: 3 }}>
              <SkippedRowsPanel token={session.token} run={s} canExport={canSync} />
            </Box>
          )}

          {/* And what the pull recorded as it ran. Last because it is the
              deepest thing here — the counters above answer "what landed", the
              worklist answers "what to fix", and this answers "what happened",
              which is the only one of the three that can explain a run that
              stopped an hour in. Managers and owners only, like the rows above:
              a log line is whatever the code passed to it. */}
          {s && canSync && (
            <Box sx={{ mt: 3 }}>
              <RunLogPanel token={session.token} runId={s.sync_run_id}
                           running={s.active} />
            </Box>
          )}
        </>
      )}
    </Box>
  );
}
