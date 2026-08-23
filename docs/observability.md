# Observability & Capacity Monitoring

PIE has a comprehensive observability foundation for monitoring system health, resource usage, and capacity.

## Quick Start

### Access the Dashboard

**Manager/Owner only:**

```
GET /api/v1/internal/observability/dashboard
```

Returns complete dashboard data: health, per-worker metrics, capacity, and
this organization's background jobs and syncs.

### Run Health Checks

```
GET /api/v1/internal/observability/health
```

Returns detailed health status for all system components.

### Check Capacity

```
GET /api/v1/internal/observability/capacity
```

Returns resource utilization and safe capacity headroom.

---

## Architecture

Observability is organized into cross-cutting infrastructure:

```
Application Code
    ↓
Instrumentation Middleware
    ├─ API Request/Response Tracking
    ├─ Database Query Instrumentation
    └─ Background Job Tracking
    ↓
Metrics / Workload Registry
    ├─ Counters (monotonic increments)
    ├─ Gauges (current values)
    └─ Histograms (distributions)
    ↓
Health Checks / Capacity Calculator
    ├─ Component Status
    ├─ Resource Utilization
    └─ Safe Headroom Calculation
    ↓
Dashboard Service
    └─ Aggregated JSON Export
```

**Files:**

- `backend/app/observability/metrics.py` — Counter, Gauge, Histogram
- `backend/app/observability/instrumentation.py` — API/DB middleware
- `backend/app/observability/health.py` — Health check registry
- `backend/app/observability/capacity.py` — Capacity calculation
- `backend/app/observability/dashboard.py` — Dashboard aggregation; reads
  `sync_runs` for the job and sync views
- `backend/app/ingestion/jobs.py` — the sync-run queries those views use
- `frontend/src/platform/ObservabilityDashboard.tsx` — UI component

---

## Available Metrics

### API Metrics

**Counters:**
- `api_requests_total` — Total requests by method/endpoint/status
- `api_errors_total` — Requests resulting in 4xx/5xx by endpoint

**Histograms:**
- `api_request_duration_seconds` — Latency distribution (p50, p95, p99)
- `api_request_bytes` — Request body size
- `api_response_bytes` — Response body size

**What it tells you:**
- Is API performance degrading?
- Which endpoints are slowest?
- What's the error rate?

### Database Metrics

**Counters:**
- `db_queries_total` — Total SQL queries executed
- `db_errors_total` — Query failures

**Gauges:**
- `db_connections` — Active connection count
- `db_pool_size` — Connection pool capacity

**Histograms:**
- `db_query_duration_seconds` — Query latency distribution

**What it tells you:**
- Is database becoming saturated?
- Are slow queries emerging?
- Is connection pool adequate?

### Background Jobs and ERP Syncs — not metrics, rows

These are **not** in the metrics registry, and this section used to list
twenty-five counters, gauges and histograms that said they were. They existed
as objects and no code ever incremented one: they were defined by a
`WorkloadTracker` that nothing called, so every one of them exported zero
forever, and `/observability/jobs` and `/observability/syncs` reported an idle
platform on a machine that might have had three syncs running. A metric nobody
writes is not a metric; a zero nobody wrote is not evidence of quiet.

They are gone. Both endpoints read the `sync_runs` table, which the sync
actually writes as it goes:

| Endpoint | Answers |
|---|---|
| `/observability/jobs` | active runs by phase, stalled runs, 24h completed / partial / failed, recent failures with the error text |
| `/observability/syncs` | the same rows read as syncs: active by connected company, records fetched and written, throughput |

Both are scoped to the caller's organization and both name their source in the
payload (`"source": "sync_runs"`).

**Reading them honestly:**

- `active` counts live runs. A run whose heartbeat has gone cold is under
  `stalled`, not `active` — its process is almost certainly gone, so counting
  it as work in progress overstates the load, and hiding it reports a wedged
  connection as an idle one. A sync started on the same connection reaps it;
  reading a dashboard does not.
- `recent_24h.basis` says what the window selected: runs that *started* in it.
- `PARTIAL` is counted as itself. It wrote rows and did not finish, so it is
  neither completed nor failed.
- `throughput_records_per_sec` can be `null`. That means it is not knowable —
  no run ended, or the runs that ended recorded no finish time — and
  `throughput_basis` says which. It is never `0`, because zero records per
  second is what a sync failing to move data looks like.
- `job_kinds` lists the background job kinds this deployment records. There is
  one, `erp_sync`. It is published so an empty `active` block reads as "nothing
  is running" rather than "some other job type went unmeasured".

Per-run detail — `documents_fetched`, and the per-entity counters the run
persists — lives on the run row itself and is served by the Data screen
(`routers/data_status.py`), where a person can also read the run's log lines.

---

## Health Checks

Each component reports health status:

- `HEALTHY` — Functioning normally
- `DEGRADED` — Working but experiencing issues
- `UNHEALTHY` — Not functioning
- `UNKNOWN` — Status unknown

**Components:**

- **database** — Can execute simple query?
- **pie_parser** — Engine loaded and ready?
- **scheduler** — Background job scheduler running?

---

## Capacity Calculation

The capacity calculator measures utilization of key resources and computes safe headroom.

### Utilization Metrics

For each resource, calculates utilization (0-1):

- **api_requests** — **UNKNOWN, always.** `api_requests_total` is a lifetime
  counter and no earlier sample is kept to difference it against, so no rate
  can be derived from it. It used to divide that cumulative count by a
  per-second limit, which pinned every worker at 100% "critical" permanently
  once it had served 1000 requests — a saturation alarm that was really an
  uptime counter. Reporting `null` with a stated reason is the honest answer
  until an exporter differences the counter across scrapes.
- **db_connections** — Active connections vs pool size
- **db_cpu** — Query latency as proxy for CPU load
- **workers** — Active background jobs vs worker limit

### Status Bands

For each resource:

- **Healthy** — Below warning threshold (default 75%)
- **Warning** — 75-90%
- **Critical** — Above 90%

### Safe Capacity Headroom

The "safe headroom" is the multiple of current load that can be added before hitting the critical threshold on the most saturated component.

Example:
- Current API utilization: 35%
- Current DB utilization: 42%
- Current Worker utilization: 51%
- Most saturated: Workers at 51%
- Safe capacity: (0.90 - 0.51) / 0.51 = 0.76x

Interpretation: Can add 76% more load before workers become critical.

### Recommended Actions

Based on the bottleneck:

- **API critical** → Scale API servers or implement rate limiting
- **DB connections critical** → Increase connection pool or optimize usage
- **DB CPU critical** → Optimize slow queries or upgrade database CPU
- **Workers critical** → Increase worker count or parallelize jobs

---

## Dashboard

The Operations Dashboard (`/api/v1/internal/observability/dashboard`) provides a complete view:

### Health & Capacity Tab
- Current health of each system component
- Resource utilization by component
- Safe capacity headroom
- Recommended scaling action

### Load & Performance Tab
- Current request/query rates
- API latency (p50, p95, p99)
- API error rate
- Active database operations

### Background Jobs Tab
- Active runs, by the phase each is in
- Stalled runs, counted apart from active
- 24-hour completed / partial / failed
- Recent failures and partial runs, with the error text

### ERP Sync Tab
- Active syncs, by connected company
- Stalled syncs
- 24-hour completed / partial / failed
- Throughput (records/second), or "Unknown" with the reason
- Recent issues

### Tenant Usage Tab
- Rankings of tenants by signal generation
- Helps identify heavy tenants

---

## Interpreting Alerts

### High API Error Rate
**Investigation:**
1. Check `/api/v1/internal/observability/api-performance`
2. Which endpoints are failing?
3. Check server logs for error IDs
4. Search for `error_<id>` in logs

### Database Saturated
**Investigation:**
1. Check `/api/v1/internal/observability/database`
2. Are connection limits being hit?
3. Are queries getting slower?
4. Enable `SQL_ECHO=1` to log all queries
5. Look for queries with latency > 1 second

### Background Jobs Backing Up
**Investigation:**
1. Check `/api/v1/internal/observability/jobs`
2. How many runs are queued, and how many are `stalled`? A stalled run holds
   no worker — it is a dead process whose row has not been reaped — so it is a
   different problem from a backlog
3. Are runs ending PARTIAL or FAILED? `failures` carries the error text
4. Check `/api/v1/internal/observability/syncs` for the per-connection view
5. Increase worker capacity if sustainable

### Zoho Sync Failing
**Investigation:**
1. Check `/api/v1/internal/observability/syncs`
2. Recent sync issues will show connection and error details
3. Run `/api/v1/internal/zoho/check` to verify credentials
4. Read the run's own log lines on the Data screen — rate limiting and API
   errors are reported there, per run, not as a global counter
5. Review sync skip reports for data issues

---

## Metrics Export — one worker at a time

```
GET /api/v1/internal/observability/metrics
```

**This returns one API worker's counters, not the deployment's.** The registry
behind it is a per-process singleton fed by per-process middleware, and the
deployment runs `UVICORN_WORKERS` of them (2 in both compose stacks and in
`deploy/backend.Dockerfile`). A request is answered by whichever worker the
load balancer picked: roughly `1/N` of the traffic, a different `1/N` on the
next scrape. A p99 read from a single response is computed from that worker's
samples alone — which is exactly the reading that used to be presented as the
whole platform's, with nothing in the payload to say otherwise.

The payload now says so:

```json
{
  "timestamp": "2026-08-23T10:30:45Z",
  "scope": "worker",
  "worker": "web-1:41:9f3c8a12",
  "workers_configured": 2,
  "composition": "One API worker's counters, not the deployment's. ...",
  "metrics": [
    {
      "name": "api_requests_total",
      "type": "counter",
      "value": 15432,
      "by_label": {
        "('method', 'GET'), ('status', '200')": 14982,
        "('method', 'POST'), ('status', '200')": 382,
        "('method', 'GET'), ('status', '500')": 68
      }
    }
  ]
}
```

- `worker` is this process's identity — `host:pid:rand`, the same string the
  scheduler lease is arbitrated with (`app/lease.py:holder_id`), so one process
  has one name wherever it appears. It is stable for the life of the process and
  re-derived after a fork.
- `workers_configured` is what the supervisor was *told* to start, or `null`
  when nothing declared it. Declared, not observed: a worker cannot see its
  siblings. `null` means unknown, never one.

**How to scrape it.** Poll until the `worker` values repeat; you then have all
of them. Two responses with the same `worker` are the same population, so the
delta between them is meaningful. Two with different `worker` values are not,
so:

- counters and gauges **sum** across workers;
- percentiles (`p50`, `p95`, `p99`) **do not**. p99 of the union is not the
  mean of the p99s. Compare them per worker, or alert on the worst one.

**Why there is no shared store.** Metrics are written on every request, and
putting that write on the request path is the SQLite locking incident in
CLAUDE.md §4 — a long or contended write blocked every reader, `/api/health`
included. Redis is provisioned next to the API but nothing imports it and no
feature may require it. So the export states what it is instead of pretending
to be deployment-wide.

**There is no Prometheus/OpenMetrics/OTLP exporter, deliberately.** Every
observability route is `require_manager_or_owner`, and there is no
service-account, API-key or scrape-credential mechanism in `app/authz.py`. How
a scraper authenticates is an open decision; shipping an exporter would settle
it by accident.

---

## Load Testing

### Test Scenarios

The observability infrastructure is designed to handle realistic workloads. Key test scenarios:

**Scenario 1: Normal Usage**
- Concurrent users: 10
- Quote creates/hour: 20
- Product searches/hour: 50
- Expected metrics: API < 5ms p99, DB < 20ms p99

**Scenario 2: Heavy Quote Workload**
- Quote operations/second: 5
- Each quote: 3 supply resolutions
- Expected: Parsing duration < 500ms p99

**Scenario 3: Heavy Analytics**
- Concurrent insight queries: 3
- Transaction date ranges: 6-12 months
- Expected: Query < 2s p99

**Scenario 4: ERP Sync**
- Concurrent syncs: 2 (different tenants)
- Data volume: 2 years of transactions
- Expected: No rate limiting, throughput > 5 records/sec

**Scenario 5: Mixed Workload**
- Normal usage + Quote operations + Analytics + Active sync
- Tests interaction between subsystems

Run with:

```bash
# See load testing setup in tests/
python -m pytest tests/load_test_scenarios.py
```

---

## Capacity Planning

### Current Thresholds (Configurable)

```python
# backend/app/observability/capacity.py
api_rps_limit = 1000              # Requests/second
db_connection_limit = 20          # Max connections
db_cpu_limit = 0.80               # CPU utilization (estimated)
worker_limit = 10                 # Background workers
```

### Growing Beyond Current Capacity

**Approach:**
1. Run load tests at expected scale
2. Measure utilization at each component
3. Identify the first bottleneck
4. Scale that component
5. Re-test and repeat

**Example:**
- At 50 concurrent users, DB becomes bottleneck (90% utilization)
- Options:
  - Index slow queries
  - Upgrade database CPU
  - Implement caching layer
  - Partition data by tenant

---

## Troubleshooting

### Metrics Not Updating
- Is the app running?
- Are requests being made?
- Check `/api/health` — if it 503s, schema may be out of sync
- Counters that jump around between scrapes are usually not a fault: each
  response comes from one worker. Check whether `worker` changed.

### Capacity Showing 0 Utilization
- No traffic in the measurement window
- Wait for requests to arrive, or generate test traffic
- Utilization is a best-effort estimate. Each component carries a `basis`
  saying whose load it measured: the API and database components count this
  worker's own traffic, the `workers` component counts live rows in
  `sync_runs` across the whole deployment. Do not add them together.

### Health Checks Failing
- Database issue → `/api/health` returns 503 with details
- PIE parser issue → Check logs for "pie-parser warm-up failed"
- Scheduler issue → Check logs for "auto-sync scheduler failed"

### Can't Access Dashboard
- Must be Owner or Manager role
- Check `/api/v1/admin/users` to verify role
- Token must be valid (check auth)

---

## Extending Observability

### Add a New Metric

In `backend/app/observability/metrics.py`:

```python
# In the module
my_counter = metrics.counter("my_operation_total", "My custom metric")

# In your code
my_counter.inc(labels={"operation": "example"})
```

### Add a New Health Check

In `backend/app/observability/health.py`:

```python
def check_my_service() -> tuple[HealthStatus, Optional[str]]:
    try:
        # Your check logic
        return HealthStatus.HEALTHY, "Service OK"
    except Exception as e:
        return HealthStatus.UNHEALTHY, f"Error: {str(e)}"

health.register("my_service", check_my_service)
```

### Track a New Background Job

There is no in-memory job tracker to register with, and the one that used to be
documented here never ran — see "Background Jobs and ERP Syncs" above.

A background job that wants to be visible persists its own state, the way the
sync does: a row it writes as it goes, with a heartbeat, so that every worker
can see it and it is still there after a restart. `SyncRun`
(`app/domain/models.py`) and `ingestion/jobs.py` are the pattern to follow —
`QUEUED → RUNNING → OK | PARTIAL | FAILED`, `heartbeat_at` touched at each phase
boundary, and a commit at each boundary rather than one long transaction
(CLAUDE.md §4).

Then read it in `observability/dashboard.py` and add its name to `job_kinds`,
so an empty list keeps meaning "nothing is running" rather than "something went
unmeasured".

---

## Related Documentation

- `docs/architecture.md` — System design
- `docs/development.md` — Development workflow
- `CLAUDE.md` — Code standards and layer boundaries
