import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import CardHeader from "@mui/material/CardHeader";
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
import { ErrorState, LoadingState } from "./kit";
import { authInit } from "../authFetch";

const healthStatusColor: Record<string, string> = {
  healthy: "#4caf50",
  degraded: "#ff9800",
  unhealthy: "#f44336",
  unknown: "#9e9e9e",
};

const healthStatusBg: Record<string, string> = {
  healthy: "#e8f5e9",
  degraded: "#fff3e0",
  unhealthy: "#ffebee",
  unknown: "#f5f5f5",
};

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
    <Box sx={{ p: 3 }}>
      <Typography variant="h4" sx={{ mb: 3 }}>
        PIE Operations Dashboard
      </Typography>

      <Tabs value={tabValue} onChange={(_, v) => setTabValue(v)} sx={{ mb: 2 }}
            variant="scrollable" scrollButtons="auto" allowScrollButtonsMobile>
        <Tab label="Health & Capacity" />
        <Tab label="Load & Performance" />
        <Tab label="Background Jobs" />
        <Tab label="ERP Sync" />
        <Tab label="Tenant Usage" />
      </Tabs>

      {/* Health & Capacity */}
      <TabPanel value={tabValue} index={0}>
        <Grid container spacing={3}>
          {/* System Health */}
          <Grid size={12}>
            <Card>
              <CardHeader
                title="System Health"
                subheader={new Date(data.health.timestamp).toLocaleTimeString()}
              />
              <CardContent>
                <Grid container spacing={2}>
                  {Object.entries(data.health.components).map(([name, comp]: [string, any]) => (
                    <Grid size={{ xs: 12, md: 6 }} key={name}>
                      <Paper
                        sx={{
                          p: 2,
                          backgroundColor:
                            healthStatusBg[comp.status as keyof typeof healthStatusBg],
                          border: `2px solid ${
                            healthStatusColor[comp.status as keyof typeof healthStatusColor]
                          }`,
                        }}
                      >
                        <Box sx={{ display: "flex", justifyContent: "space-between", mb: 1 }}>
                          <Typography variant="subtitle2">{comp.name}</Typography>
                          <Chip
                            label={comp.status.toUpperCase()}
                            size="small"
                            sx={{
                              backgroundColor:
                                healthStatusColor[comp.status as keyof typeof healthStatusColor],
                              color: "#fff",
                            }}
                          />
                        </Box>
                        <Typography variant="caption">{comp.message}</Typography>
                      </Paper>
                    </Grid>
                  ))}
                </Grid>
              </CardContent>
            </Card>
          </Grid>

          {/* Capacity */}
          <Grid size={12}>
            <Card>
              <CardHeader title="Resource Capacity" />
              <CardContent>
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
                        <Box sx={{ display: "flex", justifyContent: "space-between", mb: 1 }}>
                          <Typography variant="subtitle2">{comp.name}</Typography>
                          <Typography variant="body2" sx={{ fontWeight: "bold" }}>
                            {comp.percentage.toFixed(1)}%
                          </Typography>
                        </Box>
                        <LinearProgress
                          variant="determinate"
                          value={comp.current * 100}
                          sx={{
                            backgroundColor: "#e0e0e0",
                            "& .MuiLinearProgress-bar": {
                              backgroundColor:
                                comp.status === "critical"
                                  ? "#f44336"
                                  : comp.status === "warning"
                                    ? "#ff9800"
                                    : "#4caf50",
                            },
                          }}
                        />
                        <Typography variant="caption" sx={{ mt: 0.5, display: "block" }}>
                          Safe headroom: {comp.safe_capacity_multiplier.toFixed(1)}x
                        </Typography>
                        {comp.basis && (
                          <Typography variant="caption" sx={{ display: "block", color: "text.secondary" }}>
                            {comp.basis}
                          </Typography>
                        )}
                      </Box>
                    </Grid>
                  ))}
                </Grid>

                <Box sx={{ mt: 3, p: 2, backgroundColor: "#f5f5f5", borderRadius: 1 }}>
                  <Typography variant="subtitle2" sx={{ mb: 1 }}>
                    Recommended Action
                  </Typography>
                  <Typography variant="body2">{data.capacity.recommended_action}</Typography>
                </Box>
              </CardContent>
            </Card>
          </Grid>
        </Grid>
      </TabPanel>

      {/* Load & Performance */}
      <TabPanel value={tabValue} index={1}>
        <Grid container spacing={3}>
          <Grid size={{ xs: 12, md: 4 }}>
            <Card>
              <CardHeader title="API Load" />
              <CardContent>
                <Typography variant="h6">{data.load.api.requests_total}</Typography>
                <Typography variant="caption">Total Requests</Typography>
                <Typography variant="caption" sx={{ display: "block", color: "text.secondary" }}>
                  {data.load.api.basis}
                </Typography>
              </CardContent>
            </Card>
          </Grid>
          <Grid size={{ xs: 12, md: 4 }}>
            <Card>
              <CardHeader title="Database" />
              <CardContent>
                <Typography variant="h6">{data.load.database.queries_total}</Typography>
                <Typography variant="caption">Total Queries</Typography>
              </CardContent>
            </Card>
          </Grid>
          <Grid size={{ xs: 12, md: 4 }}>
            <Card>
              <CardHeader title="Background Load" />
              <CardContent>
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
              </CardContent>
            </Card>
          </Grid>

          {data.api && (
            <Grid size={12}>
              <Card>
                <CardHeader title="API Performance" />
                <CardContent>
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
                </CardContent>
              </Card>
            </Grid>
          )}
        </Grid>
      </TabPanel>

      {/* Background Jobs */}
      <TabPanel value={tabValue} index={2}>
        <Grid container spacing={3}>
          <Grid size={12}>
            <Card>
              <CardHeader
                title="Active Jobs"
                subheader={`Source: ${data.jobs.source} — ${data.jobs.recent_24h.basis}`}
              />
              <CardContent>
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
              </CardContent>
            </Card>
          </Grid>

          {data.jobs.failures.length > 0 && (
            <Grid size={12}>
              <Card>
                <CardHeader title="Recent failures and partial runs" />
                <CardContent>
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
                </CardContent>
              </Card>
            </Grid>
          )}
        </Grid>
      </TabPanel>

      {/* ERP Sync */}
      <TabPanel value={tabValue} index={3}>
        <Grid container spacing={3}>
          <Grid size={12}>
            <Card>
              <CardHeader
                title="Zoho Sync Status"
                subheader={`Source: ${data.syncs.source} — ${data.syncs.recent_24h.basis}`}
              />
              <CardContent>
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
              </CardContent>
            </Card>
          </Grid>

          {data.syncs.issues.length > 0 && (
            <Grid size={12}>
              <Card>
                <CardHeader title="Recent Issues" />
                <CardContent>
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
                </CardContent>
              </Card>
            </Grid>
          )}
        </Grid>
      </TabPanel>

      {/* Tenant Usage */}
      <TabPanel value={tabValue} index={4}>
        <Card>
          <CardHeader title="Top Tenants by Signal Generation" />
          <CardContent>
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
          </CardContent>
        </Card>
      </TabPanel>

      <Box sx={{ mt: 3, textAlign: "center" }}>
        <Typography variant="caption" sx={{ color: "#999" }}>
          Last updated: {new Date(data.timestamp).toLocaleTimeString()} | Auto-refresh every 30s
        </Typography>
      </Box>
    </Box>
  );
}
