import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Chip from "@mui/material/Chip";
import Grid from "@mui/material/Grid";
import LinearProgress from "@mui/material/LinearProgress";
import Paper from "@mui/material/Paper";
import Tab from "@mui/material/Tab";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableContainer from "@mui/material/TableContainer";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import Tabs from "@mui/material/Tabs";
import Typography from "@mui/material/Typography";
import { useCallback, useEffect, useState } from "react";
import type { PlatformSession } from "./types";
import { ErrorState, LoadingState, SectionHeader, StatusChip, type Tone } from "./kit";
import { authInit } from "../authFetch";

/** Component health, in this product's vocabulary rather than Material's.
 *
 * This file carried its own palette — `#4caf50`, `#ff9800`, `#f44336` and
 * four tinted backgrounds — which is Material's default green/amber/red and
 * visibly brighter and bluer than anything else here. A screen that states
 * whether the platform is well should not be the one screen that looks like a
 * different product, and a palette change would have moved every surface
 * except this one. `Tone` is the same scale `StatusChip` speaks everywhere
 * else, so these now follow the theme. */
const HEALTH_TONE: Record<string, Tone> = {
  healthy: "good",
  degraded: "warn",
  unhealthy: "bad",
  unknown: "neutral",
};

const HEALTH_GROUND: Record<string, string> = {
  healthy: "var(--color-accent-100)",
  degraded: "var(--caution-bg)",
  unhealthy: "var(--danger-bg)",
  unknown: "var(--color-neutral-100)",
};

/** A titled surface on this screen.
 *
 * `Card` until 2026-09, in eleven places, none of them a business entity —
 * ui-standards §2 reserves `Card` for something with an identity you could
 * open or act on, and "API Load" is a panel of readings. Thirty-three
 * Card-family elements also brought MUI's default paddings with them: a
 * `CardHeader` and a `CardContent` stacked roughly 90px of chrome above the
 * first number, on a screen whose whole job is numbers.
 *
 * A `Paper` with a heading, which is what the rest of the product uses. */
function Panel({ title, sub, children }: {
  title: string;
  sub?: string;
  children: React.ReactNode;
}) {
  return (
    <Paper variant="outlined" sx={{ p: 1.5, height: "100%" }}>
      <Box sx={{ mb: 1.25 }}>
        <Typography variant="overline" color="text.secondary" sx={{ display: "block", lineHeight: 1.3 }}>
          {title}
        </Typography>
        {sub && (
          <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
            {sub}
          </Typography>
        )}
      </Box>
      {children}
    </Paper>
  );
}

interface TabPanelProps {
  children?: React.ReactNode;
  index: number;
  value: number;
}

function TabPanel({ children, value, index }: TabPanelProps) {
  return (
    <div hidden={value !== index}>
      {value === index && <Box sx={{ p: 3 }}>{children}</Box>}
    </div>
  );
}

interface SystemHealth {
  timestamp: string;
  status: string;
  components: Record<string, any>;
}

interface LoadData {
  timestamp: string;
  // `active_requests` is null, not 0: nothing tracks in-flight requests, and the
  // server says so rather than sending a placeholder zero the screen would draw
  // as an idle system. `basis` carries that sentence.
  api: { requests_total: number; active_requests: number | null; basis: string };
  database: { queries_total: number; basis: string };
  background: {
    active_jobs: number;
    active_syncs: number;
    stalled: number;
    total_active: number;
    basis: string;
  };
}

interface CapacityData {
  timestamp: string;
  components: Array<{
    name: string;
    current: number;
    status: string;
    percentage: number;
    safe_capacity_multiplier: number;
    basis: string;
  }>;
  bottleneck: { component: string; current: number; status: string };
  // Every component says whose load it measures — the API and database figures
  // are one worker's, the worker figure is the whole deployment's. Shown, not
  // dropped: side by side without it, a reader adds them up.
  safe_capacity_headroom: { multiplier: number; message: string };
  recommended_action: string;
}

interface DashboardData {
  timestamp: string;
  health: SystemHealth;
  load: LoadData;
  capacity: CapacityData;
  jobs: any;
  syncs: any;
  api: any;
  database: any;
  tenants: any;
}

export function ObservabilityDashboard({ session }: { session: PlatformSession }) {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [tabValue, setTabValue] = useState(0);

  const loadDashboard = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch("/api/v1/internal/observability/dashboard",
        authInit({}, session.token));
      if (!response.ok) throw new Error(`${response.status}`);
      const dashboardData = await response.json();
      setData(dashboardData);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [session.token]);

  useEffect(() => {
    loadDashboard();
    const interval = setInterval(loadDashboard, 30000); // Refresh every 30s
    return () => clearInterval(interval);
  }, [loadDashboard]);

  if (loading && !data) return <LoadingState />;
  if (error) return <ErrorState error={error} onRetry={loadDashboard} />;
  if (!data) return <ErrorState error="No data" onRetry={loadDashboard} />;

  return (
    <Box>
      {/* `SectionHeader`, like every other screen, rather than a bare `h4`
          with its own margin — and sentence case, which is this product's
          voice throughout. Title Case is what the rest of this file was
          written in and what made it read as a bolt-on. */}
      <SectionHeader
        level="page"
        title="Platform health"
        sub="Whether PIE itself is well: component health, capacity headroom, request latency, background jobs and sync runs."
      />

      <Tabs value={tabValue} onChange={(_, v) => setTabValue(v)} sx={{ mb: 2 }}
            variant="scrollable" scrollButtons="auto" allowScrollButtonsMobile>
        <Tab label="Health &amp; capacity" />
        <Tab label="Load &amp; performance" />
        <Tab label="Background jobs" />
        <Tab label="ERP sync" />
        <Tab label="Tenant usage" />
      </Tabs>

      {/* Health & Capacity */}
      <TabPanel value={tabValue} index={0}>
        <Grid container spacing={3}>
          {/* System Health */}
          <Grid size={12}>
            <Panel title="System health" sub={new Date(data.health.timestamp).toLocaleTimeString()}>
                <Grid container spacing={2}>
                  {Object.entries(data.health.components).map(([name, comp]: [string, any]) => (
                    <Grid size={{ xs: 12, md: 6 }} key={name}>
                      <Paper
                        variant="outlined"
                        /* A tint and a chip, and no third cue. This carried a
                           3px left accent border as well — Impeccable's
                           detector flags that as the commonest tell of a
                           generated interface, and on these panels it was
                           right: the chip already carries the word and the
                           ground already carries the state, so the stripe was
                           a third rendering of one fact. PIE does use a left
                           border for state elsewhere (`.qi-approval`,
                           `.conn-warn`), where it is the only cue and earns
                           its keep; here it was not. */
                        sx={{
                          p: 1.5,
                          background: HEALTH_GROUND[comp.status] ?? HEALTH_GROUND.unknown,
                        }}
                      >
                        <Box sx={{ display: "flex", justifyContent: "space-between",
                                   alignItems: "center", gap: 1, mb: 0.5 }}>
                          <Typography variant="subtitle2">{comp.name}</Typography>
                          <StatusChip
                            label={comp.status}
                            tone={HEALTH_TONE[comp.status] ?? "neutral"}
                            dense
                          />
                        </Box>
                        <Typography variant="caption" color="text.secondary">
                          {comp.message}
                        </Typography>
                      </Paper>
                    </Grid>
                  ))}
                </Grid>
              </Panel>
          </Grid>

          {/* Capacity */}
          <Grid size={12}>
            <Panel title="Resource capacity">
                <Box sx={{ mb: 3 }}>
                  <Alert severity={data.capacity.bottleneck.status === "critical" ? "error" : "info"}>
                    <AlertTitle>Bottleneck: {data.capacity.bottleneck.component}</AlertTitle>
                    {data.capacity.safe_capacity_headroom.message}
                  </Alert>
                </Box>

                <Grid container spacing={3}>
                  {data.capacity.components.map((comp: any) => (
                    <Grid size={{ xs: 12, md: 6 }} key={comp.name}>
                      <Box>
                        {/* An unmeasured component reads UNKNOWN, and says why.
                          *
                          * The server returns `null` here on purpose — `basis`
                          * on the `api_requests` row explains at length that a
                          * lifetime counter cannot be differenced without a
                          * second sample, and that the previous arithmetic
                          * pinned every worker at "critical" forever. That is
                          * the CLAUDE.md §1 rule kept properly: no number is
                          * better than a wrong one.
                          *
                          * This screen then called `.toFixed()` on it and threw
                          * — the whole dashboard rendered as a blank error
                          * boundary, which is why nothing was lost by its
                          * having no route. The refusal is the content now. */}
                        <Box sx={{ display: "flex", justifyContent: "space-between",
                                   alignItems: "center", gap: 1, mb: 1 }}>
                          <Typography variant="subtitle2">{comp.name}</Typography>
                          {comp.percentage === null || comp.percentage === undefined
                            ? <StatusChip label="not measured" tone="neutral" dense />
                            : (
                              <Typography variant="body2" sx={{ fontWeight: 700 }}>
                                {comp.percentage.toFixed(1)}%
                              </Typography>
                            )}
                        </Box>
                        <LinearProgress
                          variant={comp.current === null || comp.current === undefined
                            ? "indeterminate" : "determinate"}
                          value={comp.current === null || comp.current === undefined
                            ? undefined : comp.current * 100}
                          /* The bar is an encoding with a legend beside it —
                             the percentage is printed above and the state is
                             named below — so hue here reinforces rather than
                             carries, which is the §6 exception. Theme tokens
                             so it moves with the palette. */
                          sx={{
                            backgroundColor: "var(--color-neutral-200)",
                            "& .MuiLinearProgress-bar": {
                              backgroundColor:
                                comp.status === "critical"
                                  ? "var(--danger-fg)"
                                  : comp.status === "warning"
                                    ? "var(--warn)"
                                    : "var(--color-accent)",
                            },
                          }}
                        />
                        {comp.safe_capacity_multiplier !== null
                          && comp.safe_capacity_multiplier !== undefined && (
                          <Typography variant="caption" sx={{ mt: 0.5, display: "block" }}>
                            Safe headroom: {comp.safe_capacity_multiplier.toFixed(1)}x
                          </Typography>
                        )}
                        {comp.basis && (
                          <Typography variant="caption" sx={{ display: "block", color: "text.secondary" }}>
                            {comp.basis}
                          </Typography>
                        )}
                      </Box>
                    </Grid>
                  ))}
                </Grid>

                <Box sx={{ mt: 2, p: 1.5, background: "var(--color-neutral-100)",
                           borderRadius: "var(--radius-md)" }}>
                  <Typography variant="subtitle2" sx={{ mb: 1 }}>
                    Recommended action
                  </Typography>
                  <Typography variant="body2">{data.capacity.recommended_action}</Typography>
                </Box>
              </Panel>
          </Grid>
        </Grid>
      </TabPanel>

      {/* Load & Performance */}
      <TabPanel value={tabValue} index={1}>
        <Grid container spacing={3}>
          <Grid size={{ xs: 12, md: 4 }}>
            <Panel title="API load">
                <Typography variant="h6">{data.load.api.requests_total}</Typography>
                <Typography variant="caption">Total Requests</Typography>
                <Typography variant="caption" sx={{ display: "block", color: "text.secondary" }}>
                  {data.load.api.basis}
                </Typography>
              </Panel>
          </Grid>
          <Grid size={{ xs: 12, md: 4 }}>
            <Panel title="Database">
                <Typography variant="h6">{data.load.database.queries_total}</Typography>
                <Typography variant="caption">Total Queries</Typography>
              </Panel>
          </Grid>
          <Grid size={{ xs: 12, md: 4 }}>
            <Panel title="Background load">
                <Typography variant="h6">{data.load.background.total_active}</Typography>
                <Typography variant="caption">Active Operations</Typography>
                {data.load.background.stalled > 0 && (
                  <Chip
                    size="small"
                    color="warning"
                    label={`${data.load.background.stalled} stalled`}
                    sx={{ mt: 1 }}
                  />
                )}
              </Panel>
          </Grid>

          {data.api && (
            <Grid size={12}>
              <Panel title="API performance">
                  <Grid container spacing={2}>
                    <Grid size={{ xs: 12, md: 4 }}>
                      <Typography variant="caption">P50 Latency</Typography>
                      <Typography variant="h6">
                        {data.api.latency_ms?.p50?.toFixed(0) || "N/A"} ms
                      </Typography>
                    </Grid>
                    <Grid size={{ xs: 12, md: 4 }}>
                      <Typography variant="caption">P95 Latency</Typography>
                      <Typography variant="h6">
                        {data.api.latency_ms?.p95?.toFixed(0) || "N/A"} ms
                      </Typography>
                    </Grid>
                    <Grid size={{ xs: 12, md: 4 }}>
                      <Typography variant="caption">Error Rate</Typography>
                      <Typography variant="h6">{data.api.error_rate?.toFixed(2) || "0"}%</Typography>
                    </Grid>
                  </Grid>
                </Panel>
            </Grid>
          )}
        </Grid>
      </TabPanel>

      {/* Background Jobs */}
      <TabPanel value={tabValue} index={2}>
        <Grid container spacing={3}>
          <Grid size={12}>
            <Panel title="Active jobs" sub={`Source: ${data.jobs.source} — ${data.jobs.recent_24h.basis}`}>
                <Typography variant="body2">
                  Active: {data.jobs.active.count} | Completed (24h): {data.jobs.recent_24h.completed} |
                  Partial (24h): {data.jobs.recent_24h.partial} |
                  Failed (24h): {data.jobs.recent_24h.failed}
                </Typography>
                {data.jobs.stalled.count > 0 && (
                  <Alert severity="warning" sx={{ mt: 2 }}>
                    <AlertTitle>{data.jobs.stalled.count} stalled</AlertTitle>
                    {data.jobs.stalled.detail}
                  </Alert>
                )}
                <Box sx={{ mt: 2 }}>
                  <Typography variant="caption">By phase:</Typography>
                  <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap", mt: 1 }}>
                    {Object.entries(data.jobs.active.by_phase).map(([phase, count]: [string, any]) => (
                      <Chip key={phase} label={`${phase}: ${count}`} size="small" />
                    ))}
                  </Box>
                </Box>
                <Typography variant="caption" sx={{ mt: 2, display: "block", color: "text.secondary" }}>
                  Job kinds recorded: {data.jobs.job_kinds.join(", ")}. Nothing else runs as a
                  background job here, so an empty list is an idle platform rather than an
                  unmeasured one.
                </Typography>
              </Panel>
          </Grid>

          {data.jobs.failures.length > 0 && (
            <Grid size={12}>
              <Panel title="Recent failures and partial runs">
                  <TableContainer>
                    <Table size="small">
                      <TableHead>
                        <TableRow>
                          <TableCell>Job Kind</TableCell>
                          <TableCell>Outcome</TableCell>
                          <TableCell>Error</TableCell>
                          <TableCell>Started</TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {data.jobs.failures.map((failure: any) => (
                          <TableRow key={failure.sync_run_id}>
                            <TableCell>{failure.job_kind}</TableCell>
                            <TableCell>
                              <Chip
                                size="small"
                                label={failure.status}
                                color={failure.status === "FAILED" ? "error" : "warning"}
                              />
                            </TableCell>
                            <TableCell sx={{ maxWidth: 300, wordBreak: "break-word" }}>
                              {failure.error ?? "No error recorded"}
                            </TableCell>
                            <TableCell>
                              {failure.timestamp
                                ? new Date(failure.timestamp).toLocaleTimeString()
                                : "—"}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </TableContainer>
                </Panel>
            </Grid>
          )}
        </Grid>
      </TabPanel>

      {/* ERP Sync */}
      <TabPanel value={tabValue} index={3}>
        <Grid container spacing={3}>
          <Grid size={12}>
            <Panel title="Sync status" sub={`Source: ${data.syncs.source} — ${data.syncs.recent_24h.basis}`}>
                <Grid container spacing={2}>
                  <Grid size={{ xs: 12, md: 3 }}>
                    <Typography variant="caption">Active Syncs</Typography>
                    <Typography variant="h6">{data.syncs.active.count}</Typography>
                  </Grid>
                  <Grid size={{ xs: 12, md: 3 }}>
                    <Typography variant="caption">Completed (24h)</Typography>
                    <Typography variant="h6">{data.syncs.recent_24h.completed}</Typography>
                  </Grid>
                  <Grid size={{ xs: 12, md: 3 }}>
                    <Typography variant="caption">Partial / Failed (24h)</Typography>
                    <Typography variant="h6">
                      {data.syncs.recent_24h.partial} / {data.syncs.recent_24h.failed}
                    </Typography>
                  </Grid>
                  <Grid size={{ xs: 12, md: 3 }}>
                    <Typography variant="caption">Throughput</Typography>
                    {/* null is "not knowable", and it must not render as 0 rec/s —
                        that is what a sync moving no data looks like. */}
                    <Typography variant="h6">
                      {data.syncs.recent_24h.throughput_records_per_sec === null
                        ? "Unknown"
                        : `${data.syncs.recent_24h.throughput_records_per_sec.toFixed(1)} rec/s`}
                    </Typography>
                    <Typography variant="caption" sx={{ color: "text.secondary" }}>
                      {data.syncs.recent_24h.throughput_basis}
                    </Typography>
                  </Grid>
                </Grid>
                {data.syncs.stalled.count > 0 && (
                  <Alert severity="warning" sx={{ mt: 2 }}>
                    <AlertTitle>{data.syncs.stalled.count} stalled</AlertTitle>
                    {data.syncs.stalled.detail}
                  </Alert>
                )}
                <Box sx={{ mt: 2, display: "flex", gap: 1, flexWrap: "wrap" }}>
                  {Object.entries(data.syncs.active.by_connection).map(
                    ([connection, count]: [string, any]) => (
                      <Chip key={connection} size="small" label={`${connection}: ${count}`} />
                    ),
                  )}
                </Box>
              </Panel>
          </Grid>

          {data.syncs.issues.length > 0 && (
            <Grid size={12}>
              <Panel title="Recent issues">
                  <TableContainer>
                    <Table size="small">
                      <TableHead>
                        <TableRow>
                          <TableCell>Company</TableCell>
                          <TableCell>Outcome</TableCell>
                          <TableCell>Error</TableCell>
                          <TableCell>Started</TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {data.syncs.issues.map((issue: any) => (
                          <TableRow key={issue.sync_run_id}>
                            <TableCell>{issue.connection_id ?? "All companies"}</TableCell>
                            <TableCell>
                              <Chip
                                size="small"
                                label={issue.status}
                                color={issue.status === "FAILED" ? "error" : "warning"}
                              />
                            </TableCell>
                            <TableCell sx={{ maxWidth: 300, wordBreak: "break-word" }}>
                              {issue.error ?? "No error recorded"}
                            </TableCell>
                            <TableCell>
                              {issue.timestamp
                                ? new Date(issue.timestamp).toLocaleTimeString()
                                : "—"}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </TableContainer>
                </Panel>
            </Grid>
          )}
        </Grid>
      </TabPanel>

      {/* Tenant Usage */}
      <TabPanel value={tabValue} index={4}>
        <Panel title="Top tenants by signal generation">
            {data.tenants.error ? (
              <Alert severity="error">{data.tenants.error}</Alert>
            ) : (
              <TableContainer>
                <Table>
                  <TableHead>
                    <TableRow>
                      <TableCell>Organization ID</TableCell>
                      <TableCell align="right">Signals Generated</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {data.tenants.tenants.map((tenant: any, idx: number) => (
                      <TableRow key={idx}>
                        <TableCell>{tenant.organization_id}</TableCell>
                        <TableCell align="right">{tenant.signals_generated}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            )}
          </Panel>
      </TabPanel>

      <Box sx={{ mt: 3, textAlign: "center" }}>
        <Typography variant="caption" color="text.secondary">
          Last updated: {new Date(data.timestamp).toLocaleTimeString()} | Auto-refresh every 30s
        </Typography>
      </Box>
    </Box>
  );
}
