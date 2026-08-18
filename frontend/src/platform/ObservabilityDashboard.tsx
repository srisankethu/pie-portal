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
  api: { requests_total: number; active_requests: number };
  database: { queries_total: number; active_jobs: number };
  background: { active_jobs: number; active_syncs: number; total_active: number };
}

interface CapacityData {
  timestamp: string;
  components: Array<{
    name: string;
    current: number;
    status: string;
    percentage: number;
    safe_capacity_multiplier: number;
  }>;
  bottleneck: { component: string; current: number; status: string };
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
              <CardHeader title="Active Jobs" />
              <CardContent>
                <Typography variant="body2">
                  Active: {data.jobs.active.count} | Completed (24h): {data.jobs.recent_24h.completed} |
                  Failed (24h): {data.jobs.recent_24h.failed}
                </Typography>
                <Box sx={{ mt: 2 }}>
                  <Typography variant="caption">By Type:</Typography>
                  <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap", mt: 1 }}>
                    {Object.entries(data.jobs.active.by_type).map(([type, count]: [string, any]) => (
                      <Chip key={type} label={`${type}: ${count}`} size="small" />
                    ))}
                  </Box>
                </Box>
              </CardContent>
            </Card>
          </Grid>

          {data.jobs.failures.length > 0 && (
            <Grid size={12}>
              <Card>
                <CardHeader title="Recent Failures" />
                <CardContent>
                  <TableContainer>
                    <Table size="small">
                      <TableHead>
                        <TableRow>
                          <TableCell>Job Type</TableCell>
                          <TableCell>Error</TableCell>
                          <TableCell>Time</TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {data.jobs.failures.map((failure: any, idx: number) => (
                          <TableRow key={idx}>
                            <TableCell>{failure.job_type}</TableCell>
                            <TableCell sx={{ maxWidth: 300, wordBreak: "break-word" }}>
                              {failure.error}
                            </TableCell>
                            <TableCell>{new Date(failure.timestamp).toLocaleTimeString()}</TableCell>
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
              <CardHeader title="Zoho Sync Status" />
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
                    <Typography variant="caption">Failed (24h)</Typography>
                    <Typography variant="h6">{data.syncs.recent_24h.failed}</Typography>
                  </Grid>
                  <Grid size={{ xs: 12, md: 3 }}>
                    <Typography variant="caption">Throughput</Typography>
                    <Typography variant="h6">
                      {data.syncs.recent_24h.throughput_records_per_sec?.toFixed(1) || 0} rec/s
                    </Typography>
                  </Grid>
                </Grid>
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
                          <TableCell>Type</TableCell>
                          <TableCell>Tenant</TableCell>
                          <TableCell>Error</TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {data.syncs.issues.map((issue: any, idx: number) => (
                          <TableRow key={idx}>
                            <TableCell>{issue.sync_type}</TableCell>
                            <TableCell>{issue.tenant}</TableCell>
                            <TableCell>{issue.error}</TableCell>
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
