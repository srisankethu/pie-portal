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
- `backend/app/observability/exposition.py` — Prometheus text rendering (pure;
  no `prometheus_client`, deliberately — see the module docstring)
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
      "help": "Total API requests",
      "value": 15432,
      "label_series": [
        {"labels": {"endpoint": "/api/v1/quotes/{quote_id}", "method": "GET",
                    "status": "200"}, "value": 14982},
        {"labels": {"endpoint": "/api/v1/quotes", "method": "POST",
                    "status": "200"}, "value": 382},
        {"labels": {"endpoint": "/api/v1/quotes/{quote_id}", "method": "GET",
                    "status": "500"}, "value": 68}
      ],
      "label_sets": 3,
      "label_capacity": 4096,
      "unattributed": 0
    }
  ]
}
```

- `label_series` is a list, not the dict of stringified Python tuples it used to
  be (`"(('method', 'GET'), ('status', 200))"`) — no consumer could use those
  without parsing a Python repr out of JSON, and the exporter needs them
  structured. `endpoint` is a route template; see "Label cardinality" below.
- `label_sets` against `label_capacity` says whether the breakdown is complete.
  `unattributed` is what the breakdown does not account for: increments with no
  labels, plus anything past the cap. `sum(label_series) + unattributed` always
  equals `value`.
- A **gauge** has no `label_series`. It never had a reader, and the field it
  used to carry was an unbounded dict — removed, the way `Histogram`'s was.

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

---

## Prometheus exposition

```
GET /api/v1/internal/observability/prometheus
Authorization: Bearer $METRICS_SCRAPE_TOKEN
```

`text/plain; version=0.0.4`. This section used to say there was deliberately no
exporter, because how a scraper authenticates was an open decision and shipping
one would have settled it by accident. It is settled now, on purpose, and the
three decisions behind it are below.

### Authentication is a static token, and only this route reads it

`METRICS_SCRAPE_TOKEN` (`app/config.py`), compared with `hmac.compare_digest` in
`routers/internal._require_scrape_token`. Prometheus supports `bearer_token`
natively, so nothing new is invented on the scraper's side.

Not a user, a role or a service account. A scraper is not a person: it has no
organization, reads no tenant rows, and would hold its credential in a config
file on a monitoring host, presented every 15 seconds forever. A principal would
make that credential replayable against every other route; a value read by one
dependency cannot be.

**When the token is unset the endpoint answers 401 — the same 401 a wrong token
gets.** Serving unauthenticated when nothing is configured is how an
observability surface ends up public on the deployment that never set up
monitoring, which is most of them. A 500 would turn a configuration state into
an error someone has to chase, and would announce to anyone asking that this
deployment has no scrape token. Refusing identically to a bad token tells an
unauthenticated caller nothing either way. The empty-token guard runs *before*
the comparison, because `compare_digest("", "")` is `True`.

The endpoint opens no database session. It is polled every 15 seconds for the
life of the deployment, and a read on that path — let alone a write — is how the
SQLite locking incident in CLAUDE.md §4 starts.

### Every series carries `worker`, and that decides what you may sum

`MetricRegistry` is a per-process singleton and the deployment runs
`UVICORN_WORKERS` of them, so a scrape reaches one worker. Without the label,
two workers produce one series that saws up and down as the load balancer picks
between them. With it:

| Metric type | Across workers | Query |
|---|---|---|
| Counter (`*_total`) | **Sums.** Each worker counts its own traffic; the deployment's total is their sum. | `sum(rate(api_requests_total[5m]))` |
| Gauge (`db_connections`, `db_pool_size`) | **Sums** — each worker holds its own pool. | `sum(db_connections)` |
| Summary quantiles (`quantile="0.99"`) | **Do not.** p99 of the union is not the mean of the p99s. | `max(api_request_duration_seconds{quantile="0.99"})` — compare per worker, or alert on the worst |
| Summary `_sum` / `_count` | **Sum.** They are lifetime totals. | `sum(rate(api_request_duration_seconds_sum[5m])) / sum(rate(api_request_duration_seconds_count[5m]))` for a mean |
| `pie_workers_configured` | **Same on every worker** — read it with `max()`. | `max(pie_workers_configured)` |

`pie_workers_configured` is what the supervisor was *told* to start, and it is
**absent** when nothing declared it rather than `1`. Absence is a gap a scraper
can notice; `1` would tell it a single scrape had the whole deployment. Use it
to check coverage: `count(count by (worker) (api_requests_total)) < max(pie_workers_configured)`
means you are not seeing every worker.

### Histograms are exported as summaries, not histograms

A Prometheus histogram is cumulative bucket counts. This registry keeps no
bucket counts — it keeps a bounded ring of recent samples and computes
quantiles from it. `# TYPE histogram` with no `_bucket` series would make
`histogram_quantile()` return nothing on a metric that looks like it should
work. So a summary, which is exactly what this is: pre-computed quantiles that
do not aggregate, beside a `_sum` and `_count` that do.

The two describe different populations, which the JSON export already states and
the exposition preserves: `_sum` and `_count` are **lifetime**, the quantiles
are over the **last 2048 observations** (`DEFAULT_SAMPLE_CAPACITY`). A histogram
with no observations emits `_sum` and `_count` and **no quantile at all** —
never a `0`, which reads as a suspiciously fast p99.

### The `api_requests` rate

Do not compute one in the exporter. `capacity.calculate_api_utilization` returns
`null` because a rate needs two samples and an interval and this process holds
one lifetime counter — see "Utilization Metrics" above. Exporting the raw
counter *is* the fix: `rate(api_requests_total[5m])` differences it across
scrapes, and the second sample is the next scrape. The capacity component stays
`null` on purpose, because the request rate is a deployment question and one
worker differencing its own counter would answer a different one while looking
like the same number.

### Label cardinality, as a number

The `endpoint` label is the **matched route template**
(`/api/v1/quotes/{quote_id}`), never the raw path — `instrumentation.route_template`.
The raw path was the previous behaviour and it is the classic way to kill a
TSDB: one time series per quote id.

- `api_requests_total{worker,method,endpoint,status}` — bounded by the routing
  table: **183 path × method pairs** against the dozen or so status codes this
  application returns, so **under ~2,400 series per worker** worst case, and two
  orders of magnitude fewer in practice. Raw paths are bounded by nothing.
- `api_errors_total{worker,endpoint,status}` — a subset of the same product.
  It used to carry `{endpoint, error: <exception class>}` on the unhandled path:
  a second label *set* on one metric, so `sum by (status)` swept every unhandled
  error into an empty bucket, and an exception class name is bounded only by
  what happens to be importable. The class is in the log line with a traceback,
  which is where an unbounded string belongs.
- `<unmatched>` is the endpoint label for a request that matched no route. A
  404 has no template, and falling back to the raw path would reintroduce the
  unboundedness on exactly the traffic most likely to be a scanner.
- `Counter` refuses to grow past `MAX_LABEL_SETS` (4096) regardless. That is a
  backstop for the *next* call site, deliberately set above what the routing
  table can produce so it never truncates real traffic. When it does bite,
  nothing is lost: the increment lands in the counter's total and is published
  as a single `label_overflow="true"` series, so the parts still sum to the
  whole.
- `Gauge` keeps **no** per-label breakdown. Removed rather than capped,
  following `Histogram`: no call site passed labels to a gauge and no reader
  ever read the field.

### There is no alerting here, and that is a decision

No SMTP, no webhook, no alert rules as code. There is no notification
destination anywhere in this repository, and a threshold with no destination is
a threshold nobody acts on — it would be configuration that looks like a
control. What is worth watching is written down instead, so that whoever wires
up an Alertmanager has the conditions rather than having to invent them:

| SLI | Expression | Fires when | Why it matters |
|---|---|---|---|
| API error rate | `sum(rate(api_errors_total[5m])) / sum(rate(api_requests_total[5m]))` | `> 0.02` for 10 min | 4xx and 5xx together; a spike in one endpoint's 403s is a broken role scope, not just noise |
| Server errors | `sum(rate(api_errors_total{status="500"}[5m]))` | `> 0` for 5 min | Every 500 has an `error_id` in the log (`main.unhandled_error`) |
| API latency | `max(api_request_duration_seconds{quantile="0.99"})` | `> 1.0` for 15 min | Per worker — never averaged across them |
| Database latency | `max(db_query_duration_seconds{quantile="0.99"})` | `> 0.5` for 15 min | On SQLite this is usually lock contention; see CLAUDE.md §4 |
| Database errors | `sum(rate(db_errors_total[5m]))` | `> 0` for 10 min | |
| Connection pool | `max(db_connections / db_pool_size)` | `> 0.9` for 10 min | Both gauges are per worker, so the ratio is too |
| Worker coverage | `count(count by (worker) (api_requests_total)) < max(pie_workers_configured)` | for 15 min | A worker that stopped serving, or a scrape target that is missing |
| Label overflow | `sum(api_requests_total{label_overflow="true"}) > 0` | ever | Something started passing an unbounded label; find it before the TSDB does |

Sync and job health are deliberately **not** in that table. They live in
`sync_runs` and are served by `/observability/jobs` and `/observability/syncs`
— rows, not metrics, for the reasons under "Background Jobs and ERP Syncs".

### A scrape config

```yaml
scrape_configs:
  - job_name: pie-portal
    metrics_path: /api/v1/internal/observability/prometheus
    scrape_interval: 15s
    authorization:
      type: Bearer
      credentials_file: /etc/prometheus/pie-scrape-token
    static_configs:
      - targets: ["pie-api:8000"]
```

Point it at each replica rather than at a load balancer. Behind a balancer a
scrape lands on one worker at random, so a counter appears to jump backwards
between scrapes — `rate()` reads that as a counter reset and drops the sample.
The `worker` label makes the problem visible; it does not make it correct.

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
