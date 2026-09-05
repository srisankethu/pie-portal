// The platform's own vitals: is it healthy, how loaded is it, and did the last
// sync finish.
//
// The screen was written and never reached — no route, no nav entry, no
// importer — so nothing here had ever run in a browser. Wiring it up turned up
// what that costs, and the fixes are the bulk of this file's history:
//
//   - `capacity.bottleneck` and `capacity.safe_capacity_headroom` are `null`
//     whenever no component reported a usable figure, which is precisely the
//     state a fresh deployment is in and precisely when somebody opens this
//     screen. Three `.toFixed` calls and two property reads went straight
//     through them, so the first ever render would have been a white page.
//   - `error_rate` is `null` over a worker that has served no requests, with a
//     comment in `dashboard.py` citing CLAUDE.md §1 for why it is not zero.
//     This screen rendered `error_rate?.toFixed(2) || "0"` — the benign default
//     the server had refused to invent, drawn as a green 0%.
//   - `failures` is the ten most recent and `issues` the five most recent;
//     both were drawn as if complete. `recent_24h` carries the true totals, so
//     each list now says which of the two it is showing. That is the
//     `SkippedRowsPanel` lesson: a twenty-row preview of a 1,304-row problem is
//     read as the whole problem by the third time of looking at it.
//   - `capacity.unmeasured`, `jobs.history` and `tenants.scope` were all
//     published by the server for a reader and dropped by the screen. They are
//     the fields that tell a quiet window from a dead one and a row-level-
//     security-scoped tenant list from the whole deployment's.
//
// Its three tables were hand-written and are `DataGrid`s now, per
// ui-standards §3. They sit in that document's "still arguable" category — a
// capped sample rather than an unbounded list — and the cap is what decides it
// the other way: each is a server-side truncation of a set whose size is the
// business's (runs that failed today, organizations on the deployment), so the
// row count is the business's and the screen was hiding that it was truncated.
// A grid also gives the two questions these panels are actually opened with —
// "which company keeps failing" and "when did this start" — a sort each.
//
// Surfaces are `Paper` per §2 (nothing here is a business entity), status is a
// `StatusChip` per §6, and colour comes from the theme rather than from the
// eight hex literals that used to be at the top of this file.

import { useCallback, useEffect, useState } from "react";
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Grid from "@mui/material/Grid";
import LinearProgress from "@mui/material/LinearProgress";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import Typography from "@mui/material/Typography";

import { formatDateTime } from "../when";
import { papi } from "./api";
import { DataGrid, numeric, type ColDef } from "./DataGrid";
import {
  EmptyState, ErrorState, LoadingState, MetricCard, SectionHeader, StatusChip, type Tone,
} from "./kit";
import type {
  CapacityComponent, ObservabilityDashboard as Dashboard, PlatformSession, RunIssue,
  TenantUsageRow,
} from "./types";

/** `HealthStatus.value` as a tone. `unknown` is `neutral` rather than a colour:
 *  the registry ranks it above healthy and below degraded precisely because it
 *  is not a verdict, and painting it green or amber would make it one. */
const HEALTH_TONE: Record<string, Tone> = {
  healthy: "good", degraded: "warn", unhealthy: "bad", unknown: "neutral",
};

/** A capacity component's status as a bar colour. Never the only cue — the
 *  percentage is written beside it, and an unmeasured component gets no bar at
 *  all rather than an empty one. */
const CAPACITY_COLOR: Record<string, "success" | "warning" | "error"> = {
  healthy: "success", warning: "warning", critical: "error",
};

/** A run outcome. PARTIAL is amber and FAILED is red because they are
 *  different facts: a partial run wrote rows and did not finish. */
const OUTCOME_TONE: Record<string, Tone> = { FAILED: "bad", PARTIAL: "warn" };

/** How many of a truncated list are on screen, in words.
 *
 *  The server sends the most recent ten failures and five sync issues; the
 *  window totals sit beside them in the same payload. "all 4" and "the 5 most
 *  recent of 23" must not look the same, which is the only reason this exists.
 */
function coverage(shown: number, total: number, noun: string): string {
  if (total === 0) return `no ${noun} in the last 24 hours`;
  if (shown >= total) return `all ${total} in the last 24 hours`;
  return `the ${shown} most recent of ${total} in the last 24 hours`;
}

/** A number the server declined to state, said as such.
 *
 *  `??`, never `||`: a genuine 0 ms or 0% is a measurement, and the operator
 *  `||` cannot tell it from the null it is standing in for. That substitution
 *  is what put "0" under Error Rate on a worker that had answered nothing. */
function stated(value: number | null | undefined, format: (n: number) => string): string {
  return value === null || value === undefined ? "Not known" : format(value);
}

/** A titled surface. `Paper`, because none of these panels wraps a business
 *  entity — §2. Local rather than in `kit.tsx`: it is eight uses in one file,
 *  and `SectionHeader` already owns the heading ramp inside it. */
function Panel({ title, sub, children }: {
  title: string;
  sub?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <Paper variant="outlined" sx={{ p: 2, height: "100%" }}>
      <SectionHeader level="widget" title={title} sub={sub} />
      {children}
    </Paper>
  );
}

function TabPanel({ children, value, index, id }: {
  children?: React.ReactNode;
  index: number;
  value: number;
  id: string;
}) {
  return (
    <div role="tabpanel" hidden={value !== index}
         id={`obs-panel-${id}`} aria-labelledby={`obs-tab-${id}`}>
      {value === index && <Box sx={{ pt: 3 }}>{children}</Box>}
    </div>
  );
}

/* ── the three lists ─────────────────────────────────────────────────────────
 * The run id rides as a second line under the company rather than as a column
 * of its own: it is what correlates a row with the server log, and nobody
 * sorts by it. `twoLineRows` is what makes room.
 */

/** Columns shared by the two run lists.
 *
 *  One set, not two that drift: these are the same rows read twice —
 *  `get_background_jobs` and `get_zoho_sync_status` both select FAILED and
 *  PARTIAL out of one window over `sync_runs`, and `JobFailure` is a `RunIssue`
 *  with a `job_kind` on it.
 *
 *  That `job_kind` gets no column. The payload publishes `job_kinds:
 *  ["erp_sync"]` — one kind, so the cell would be the same word on every row —
 *  and the panel states it in its subheading, where it says something. */
const RUN_COLUMNS: ColDef<RunIssue>[] = [
  {
    field: "timestamp", headerName: "Started", width: 190, flex: 0,
    // Newest first without being asked: the row somebody opened this panel
    // for is the one that just happened.
    sort: "desc",
    valueFormatter: (p) => formatDateTime(p.value as string | null),
  },
  {
    headerName: "Company", flex: 1, minWidth: 200,
    valueGetter: (p) => p.data?.connection_id ?? "All companies",
    cellRenderer: (p: { data?: RunIssue }) => p.data ? (
      <div>
        <b>{p.data.connection_id ?? "All companies"}</b>
        <div className="fsrc">{p.data.sync_run_id}</div>
      </div>
    ) : null,
  },
  {
    field: "status", headerName: "Outcome", width: 130, flex: 0,
    cellRenderer: (p: { data?: RunIssue }) => p.data ? (
      <StatusChip
        label={p.data.status}
        tone={OUTCOME_TONE[p.data.status] ?? "neutral"}
        tip={p.data.status === "PARTIAL"
          ? "Wrote rows and did not finish. Counted apart from both completed and failed, because either would misstate what arrived."
          : "Ended without writing a complete result."}
      />
    ) : null,
  },
  {
    headerName: "What went wrong", flex: 1.4, minWidth: 240,
    valueGetter: (p) => p.data?.error ?? "No error recorded",
    // The full text on hover: an error long enough to matter is longer than
    // the column, and truncating it silently is how a screen stops being
    // where anybody looks.
    tooltipValueGetter: (p) => p.data?.error ?? "No error recorded",
  },
];

function RunIssueGrid({ rows, ariaLabel, empty }: {
  rows: RunIssue[];
  ariaLabel: string;
  empty: React.ReactNode;
}) {
  return (
    <DataGrid<RunIssue>
      ariaLabel={ariaLabel}
      rows={rows}
      columns={RUN_COLUMNS}
      getRowId={(r) => r.sync_run_id}
      pageSize={10}
      twoLineRows
      // No filter row: the server caps these at ten and five, every column is
      // short, and sorting answers what the panel is opened with.
      filters={false}
      empty={empty}
    />
  );
}

const TENANT_COLUMNS: ColDef<TenantUsageRow>[] = [
  {
    field: "organization_id", headerName: "Organization", flex: 1, minWidth: 220,
    cellClass: "mono",
  },
  numeric<TenantUsageRow>(
    "signals_generated", "Signals generated", (n) => n.toLocaleString("en-IN"),
    { width: 200, flex: 0, sort: "desc" }),
];

/* ── the screen ──────────────────────────────────────────────────────────── */

export function ObservabilityDashboard({ session }: { session: PlatformSession }) {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await papi.observability(session.token));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [session.token]);

  useEffect(() => {
    load();
    const timer = setInterval(load, 30_000);
    return () => clearInterval(timer);
  }, [load]);

  if (loading && !data) return <LoadingState rows={5} height={72} />;
  // Never an empty state: "we could not look" is not "nothing is wrong", and
  // this is the one screen where confusing the two is the whole failure.
  if (error && !data) return <ErrorState error={error} onRetry={load} busy={loading} />;
  if (!data) return <ErrorState error="No data" onRetry={load} busy={loading} />;

  const { health, load: current, capacity, api, jobs, syncs, tenants } = data;
  const jobsBad = jobs.recent_24h.failed + jobs.recent_24h.partial;
  const syncsBad = syncs.recent_24h.failed + syncs.recent_24h.partial;

  const TABS = ["Health & capacity", "Load", "Background jobs", "ERP sync", "Tenant usage"];

  return (
    <Box>
      <SectionHeader
        title="System health"
        sub="What the platform itself is doing: whether every component answers, how much room is left, and whether the last sync finished."
        actions={
          <StatusChip
            label={health.status.toUpperCase()}
            tone={HEALTH_TONE[health.status] ?? "neutral"}
            tip="The worst thing any registered check reports. UNKNOWN outranks healthy on purpose — a component whose state cannot be determined needs attention."
          />
        }
      />

      {/* A refresh that failed while an earlier response is still on screen.
          Said out loud rather than left to the timestamp at the foot: figures
          that stop moving look like a quiet system. */}
      {error && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          <AlertTitle>These figures are not being refreshed</AlertTitle>
          The last poll failed: {error}. What follows is the reading from{" "}
          {formatDateTime(data.timestamp)}.
        </Alert>
      )}

      <Tabs value={tab} onChange={(_, v) => setTab(v)} variant="scrollable"
            scrollButtons="auto" allowScrollButtonsMobile
            aria-label="Operations dashboard sections">
        {TABS.map((label, i) => (
          <Tab key={label} label={label} id={`obs-tab-${i}`}
               aria-controls={`obs-panel-${i}`} />
        ))}
      </Tabs>

      {/* ── health & capacity ── */}
      <TabPanel value={tab} index={0} id="0">
        <Grid container spacing={3}>
          <Grid size={12}>
            <Panel title="Components"
                   sub={`Every registered check, as of ${formatDateTime(health.timestamp)}.`}>
              <Grid container spacing={2}>
                {Object.entries(health.components).map(([key, comp]) => (
                  <Grid size={{ xs: 12, md: 6 }} key={key}>
                    <Paper variant="outlined" sx={{ p: 2, height: "100%" }}>
                      <Stack direction="row" spacing={1} useFlexGap
                             sx={{ justifyContent: "space-between",
                                   alignItems: "flex-start", mb: 1 }}>
                        <Typography variant="subtitle2">{comp.name}</Typography>
                        <StatusChip label={comp.status.toUpperCase()}
                                    tone={HEALTH_TONE[comp.status] ?? "neutral"} />
                      </Stack>
                      <Typography variant="caption" color="text.secondary">
                        {comp.message ?? "This check reported no detail."}
                      </Typography>
                    </Paper>
                  </Grid>
                ))}
              </Grid>
            </Panel>
          </Grid>

          <Grid size={12}>
            <Panel title="Capacity">
              {/* The refusal branch. `get_overall_capacity` returns a null
                  bottleneck and null headroom when nothing was measurable,
                  rather than a shape whose zeroes read as calm — so this says
                  the same thing rather than reading them as room to spare. */}
              {capacity.bottleneck === null || capacity.safe_capacity_headroom === null ? (
                <Alert severity="warning" sx={{ mb: 3 }}>
                  <AlertTitle>Capacity is unknown</AlertTitle>
                  {capacity.recommended_action}
                </Alert>
              ) : (
                <Alert
                  severity={capacity.bottleneck.status === "critical" ? "error"
                    : capacity.bottleneck.status === "warning" ? "warning" : "info"}
                  sx={{ mb: 3 }}
                >
                  <AlertTitle>Tightest component: {capacity.bottleneck.component}</AlertTitle>
                  {capacity.safe_capacity_headroom.message}
                </Alert>
              )}

              {/* Which components the headroom claim does not rest on. The
                  server names them; a "3.4x headroom" measured on one of four
                  components is the reassuring-looking answer §1 is about. */}
              {capacity.unmeasured.length > 0 && (
                <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                  Not measured, and therefore not in the figure above:{" "}
                  {capacity.unmeasured.join(", ")}.
                </Typography>
              )}

              <Grid container spacing={3}>
                {capacity.components.map((comp: CapacityComponent) => (
                  <Grid size={{ xs: 12, md: 6 }} key={comp.name}>
                    <Stack direction="row" spacing={1} useFlexGap
                           sx={{ justifyContent: "space-between", mb: 0.5 }}>
                      <Typography variant="subtitle2">{comp.name}</Typography>
                      <Typography variant="body2"
                                  sx={{ fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>
                        {stated(comp.percentage, (n) => `${n.toFixed(1)}%`)}
                      </Typography>
                    </Stack>
                    {/* No bar for an unmeasured component. An empty track reads
                        as "nearly idle", which is the opposite of not knowing. */}
                    {comp.percentage === null ? (
                      <StatusChip label="Not measured" tone="neutral" dense
                                  tip="This component reported no usable figure, so it is neither ranked as the bottleneck nor counted in the headroom." />
                    ) : (
                      <LinearProgress
                        variant="determinate"
                        value={Math.min(100, Math.max(0, comp.percentage))}
                        color={CAPACITY_COLOR[comp.status] ?? "inherit"}
                        aria-label={`${comp.name} utilization`}
                      />
                    )}
                    <Typography variant="caption" color="text.secondary"
                                sx={{ display: "block", mt: 0.5 }}>
                      Headroom {stated(comp.safe_capacity_multiplier, (n) => `${n.toFixed(1)}×`)}
                      {" · "}{comp.basis}
                    </Typography>
                  </Grid>
                ))}
              </Grid>

              <Paper variant="outlined" sx={{ mt: 3, p: 2, bgcolor: "action.hover" }}>
                <Typography variant="overline" color="text.secondary">
                  Recommended action
                </Typography>
                <Typography variant="body2">{capacity.recommended_action}</Typography>
              </Paper>
            </Panel>
          </Grid>
        </Grid>
      </TabPanel>

      {/* ── load ── */}
      <TabPanel value={tab} index={1} id="1">
        <Grid container spacing={3}>
          <Grid size={{ xs: 12, md: 4 }}>
            <MetricCard label="API requests" value={current.api.requests_total.toLocaleString("en-IN")}
                        sub={current.api.basis} />
          </Grid>
          <Grid size={{ xs: 12, md: 4 }}>
            <MetricCard label="Database queries" value={current.database.queries_total.toLocaleString("en-IN")}
                        sub={current.database.basis} />
          </Grid>
          <Grid size={{ xs: 12, md: 4 }}>
            <MetricCard
              label="Background operations"
              value={current.background.total_active}
              sub={current.background.basis}
              action={current.background.stalled > 0
                ? <StatusChip label={`${current.background.stalled} stalled`} tone="warn" />
                : undefined}
            />
          </Grid>

          <Grid size={12}>
            <Panel
              title="This worker's API performance"
              sub={api.basis}
            >
              <Grid container spacing={2}>
                <Grid size={{ xs: 12, md: 3 }}>
                  <MetricCard label="p50 latency"
                              value={stated(api.latency_ms?.p50, (n) => `${n.toFixed(0)} ms`)} />
                </Grid>
                <Grid size={{ xs: 12, md: 3 }}>
                  <MetricCard label="p95 latency"
                              value={stated(api.latency_ms?.p95, (n) => `${n.toFixed(0)} ms`)} />
                </Grid>
                <Grid size={{ xs: 12, md: 3 }}>
                  <MetricCard
                    label="Error rate"
                    value={stated(api.error_rate, (n) => `${n.toFixed(2)}%`)}
                    // The reason this is not "0%". A rate over no requests has
                    // no denominator, and a green zero on a worker that has
                    // answered nothing is the benign default §1 forbids.
                    sub={api.error_rate === null
                      ? "This worker has served no requests, so a rate would have no denominator."
                      : `${api.errors_total.toLocaleString("en-IN")} of ${api.requests_total.toLocaleString("en-IN")} requests`}
                  />
                </Grid>
                <Grid size={{ xs: 12, md: 3 }}>
                  <MetricCard label="Counting since" value={formatDateTime(api.counting_since)}
                              sub={`Worker ${api.worker}`} />
                </Grid>
              </Grid>
            </Panel>
          </Grid>
        </Grid>
      </TabPanel>

      {/* ── background jobs ── */}
      <TabPanel value={tab} index={2} id="2">
        <Grid container spacing={3}>
          <Grid size={12}>
            <Panel
              title="Runs"
              sub={`From ${jobs.source} — ${jobs.recent_24h.basis}. Job kinds recorded: ${jobs.job_kinds.join(", ")}; nothing else runs as a background job here, so an empty list is an idle platform rather than an unmeasured one.`}
            >
              <Grid container spacing={2}>
                <Grid size={{ xs: 6, md: 3 }}>
                  <MetricCard label="Active" value={jobs.active.count} />
                </Grid>
                <Grid size={{ xs: 6, md: 3 }}>
                  <MetricCard label="Completed (24h)" value={jobs.recent_24h.completed} />
                </Grid>
                <Grid size={{ xs: 6, md: 3 }}>
                  <MetricCard label="Partial (24h)" value={jobs.recent_24h.partial}
                              tip="Wrote rows and did not finish. Neither completed nor failed." />
                </Grid>
                <Grid size={{ xs: 6, md: 3 }}>
                  <MetricCard label="Failed (24h)" value={jobs.recent_24h.failed} />
                </Grid>
              </Grid>

              {/* The two dates that tell a quiet window from a dead one. An
                  all-zero window looks identical whether nothing was due or the
                  scheduler stopped queueing a week ago. */}
              <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
                {jobs.history.ever_run
                  ? <>Last run {formatDateTime(jobs.history.last_run_at)}; last successful run{" "}
                      {jobs.history.last_successful_run_at
                        ? formatDateTime(jobs.history.last_successful_run_at)
                        : "never"}.</>
                  : "No run has ever been recorded for this organization."}
              </Typography>

              {jobs.stalled.count > 0 && (
                <Alert severity="warning" sx={{ mt: 2 }}>
                  <AlertTitle>{jobs.stalled.count} stalled</AlertTitle>
                  {jobs.stalled.detail}
                </Alert>
              )}

              {Object.keys(jobs.active.by_phase).length > 0 && (
                <Stack direction="row" spacing={1} useFlexGap
                       sx={{ flexWrap: "wrap", mt: 2 }}>
                  {Object.entries(jobs.active.by_phase).map(([phase, count]) => (
                    <StatusChip key={phase} label={`${phase}: ${count}`} tone="info" />
                  ))}
                </Stack>
              )}
            </Panel>
          </Grid>

          <Grid size={12}>
            <Panel
              title="Failed and partial runs"
              sub={`Showing ${coverage(jobs.failures.length, jobsBad, "failed or partial runs")}.`}
            >
              <RunIssueGrid
                ariaLabel="Failed and partial background runs"
                rows={jobs.failures}
                empty={<EmptyState
                  title="Nothing failed"
                  reason="No background run failed or ended partway in the last 24 hours." />}
              />
            </Panel>
          </Grid>
        </Grid>
      </TabPanel>

      {/* ── ERP sync ── */}
      <TabPanel value={tab} index={3} id="3">
        <Grid container spacing={3}>
          <Grid size={12}>
            <Panel title="Sync" sub={`From ${syncs.source} — ${syncs.recent_24h.basis}.`}>
              <Grid container spacing={2}>
                <Grid size={{ xs: 6, md: 3 }}>
                  <MetricCard label="Active" value={syncs.active.count} />
                </Grid>
                <Grid size={{ xs: 6, md: 3 }}>
                  <MetricCard label="Completed (24h)" value={syncs.recent_24h.completed} />
                </Grid>
                <Grid size={{ xs: 6, md: 3 }}>
                  <MetricCard label="Partial / failed (24h)"
                              value={`${syncs.recent_24h.partial} / ${syncs.recent_24h.failed}`} />
                </Grid>
                <Grid size={{ xs: 6, md: 3 }}>
                  {/* null is "not knowable" and must never render as 0 rec/s —
                      that is what a sync moving no data looks like. */}
                  <MetricCard
                    label="Throughput"
                    value={stated(syncs.recent_24h.throughput_records_per_sec,
                                  (n) => `${n.toFixed(1)} rec/s`)}
                    sub={syncs.recent_24h.throughput_basis}
                  />
                </Grid>
              </Grid>

              <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
                {syncs.history.ever_run
                  ? <>Last sync {formatDateTime(syncs.history.last_run_at)}; last successful sync{" "}
                      {syncs.history.last_successful_run_at
                        ? formatDateTime(syncs.history.last_successful_run_at)
                        : "never"}.</>
                  : "No sync has ever been recorded for this organization."}
              </Typography>

              {syncs.stalled.count > 0 && (
                <Alert severity="warning" sx={{ mt: 2 }}>
                  <AlertTitle>{syncs.stalled.count} stalled</AlertTitle>
                  {syncs.stalled.detail}
                </Alert>
              )}

              {Object.keys(syncs.active.by_connection).length > 0 && (
                <Stack direction="row" spacing={1} useFlexGap
                       sx={{ flexWrap: "wrap", mt: 2 }}>
                  {Object.entries(syncs.active.by_connection).map(([connection, count]) => (
                    <StatusChip key={connection} label={`${connection}: ${count}`} tone="info" />
                  ))}
                </Stack>
              )}
            </Panel>
          </Grid>

          <Grid size={12}>
            <Panel
              title="Sync issues"
              sub={`Showing ${coverage(syncs.issues.length, syncsBad, "failed or partial syncs")}.`}
            >
              <RunIssueGrid
                ariaLabel="Failed and partial syncs"
                rows={syncs.issues}
                empty={<EmptyState
                  title="No sync issues"
                  reason="No sync failed or ended partway in the last 24 hours." />}
              />
            </Panel>
          </Grid>
        </Grid>
      </TabPanel>

      {/* ── tenant usage ── */}
      <TabPanel value={tab} index={4} id="4">
        <Panel
          title="Signals by organization"
          // Which of the two lists this is, from the server's own field rather
          // than inferred from the row count — which gets it wrong on the day a
          // deployment has one customer.
          sub={tenants.scope === "OWN_ORGANIZATION"
            ? "This organization only. The query carries no organization predicate; row-level security scoped it."
            : tenants.scope === "ALL_ORGANIZATIONS"
              ? "Every organization on this deployment — row-level security is not binding this query here."
              : undefined}
        >
          {tenants.error ? (
            <ErrorState title="Tenant usage did not load" error={tenants.error} />
          ) : (
            <DataGrid<TenantUsageRow>
              ariaLabel="Signals generated by organization"
              rows={tenants.tenants}
              columns={TENANT_COLUMNS}
              getRowId={(t) => t.organization_id}
              pageSize={25}
              filters={false}
              empty={<EmptyState
                title="No signals yet"
                reason="No organization on this deployment has generated a signal." />}
            />
          )}
        </Panel>
      </TabPanel>

      <Typography variant="caption" color="text.secondary"
                  sx={{ display: "block", mt: 3, textAlign: "center" }}>
        Read at {formatDateTime(data.timestamp)} · refreshes every 30 seconds
      </Typography>
    </Box>
  );
}
