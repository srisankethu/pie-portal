# Observability & Capacity Monitoring

PIE has a comprehensive observability foundation for monitoring system health, resource usage, and capacity.

## Quick Start

### Access the Dashboard

**Manager/Owner only:**

```
GET /api/v1/internal/observability/dashboard
```

Returns complete dashboard data: health, metrics, capacity, workload details.

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
- `backend/app/observability/workload.py` — Background job tracking
- `backend/app/observability/health.py` — Health check registry
- `backend/app/observability/capacity.py` — Capacity calculation
- `backend/app/observability/dashboard.py` — Dashboard aggregation
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

### Background Job Metrics

**Counters:**
- `jobs_created_total` — Jobs started by type
- `jobs_completed_total` — Jobs successfully completed
- `jobs_failed_total` — Job failures
- `jobs_retried_total` — Retry events
- `records_processed_total` — Records processed by job type

**Gauges:**
- `jobs_active` — Currently running jobs

**Histograms:**
- `job_duration_seconds` — Job execution time

**What it tells you:**
- Are background jobs piling up?
- What's the failure rate?
- How long do jobs take?

### Zoho Sync Metrics

**Counters:**
- `syncs_started_total` — Syncs initiated by type
- `syncs_completed_total` — Successfully completed syncs
- `syncs_failed_total` — Sync failures
- `sync_records_fetched_total` — Records pulled from ERP
- `sync_records_processed_total` — Records inserted/updated
- `sync_api_calls_total` — API calls to Zoho Books
- `sync_api_errors_total` — ERP API failures
- `sync_rate_limits_total` — Rate limit events

**Gauges:**
- `syncs_active` — Currently running syncs

**Histograms:**
- `sync_duration_seconds` — Sync execution time

**What it tells you:**
- Is ERP sync keeping up?
- Are rate limits being hit?
- What's the sync throughput?

### PIE-Specific Metrics

**Product Parsing:**
- `parsing_operations_total` — Product parsing attempts
- `parsing_failures_total` — Parsing failures
- `parsing_duration_seconds` — Parsing latency

**Quote Resolution:**
- `quote_resolutions_total` — Quote resolution attempts
- `quote_exact_matches_total` — Exact product matches
- `quote_equivalent_matches_total` — Approximate matches
- `quote_unresolved_total` — Unmatched products
- `quote_resolution_duration_seconds` — Resolution latency

**Analytics Queries:**
- `analytics_queries_total` — Analytics endpoint calls
- `analytics_duration_seconds` — Query latency

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

- **api_requests** — Requests/second vs configured limit
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
- Active job count and distribution
- 24-hour completion/failure stats
- Recent job failures with error details

### ERP Sync Tab
- Active sync count and distribution
- 24-hour completion/failure stats
- Sync throughput (records/second)
- Recent sync issues

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
2. How many jobs are stuck in QUEUED state?
3. Are jobs timing out or failing?
4. Check `/api/v1/internal/observability/syncs` for ERP sync issues
5. Increase worker capacity if sustainable

### Zoho Sync Failing
**Investigation:**
1. Check `/api/v1/internal/observability/syncs`
2. Recent sync issues will show connection and error details
3. Run `/api/v1/internal/zoho/check` to verify credentials
4. Check for rate limiting (sync_rate_limits_total)
5. Review sync skip reports for data issues

---

## Metrics Export

For external monitoring systems (Prometheus, DataDog, etc.):

```
GET /api/v1/internal/observability/metrics
```

Returns raw metrics in aggregated format:

```json
{
  "timestamp": "2026-08-15T10:30:45Z",
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
    },
    ...
  ]
}
```

Export this every 60 seconds to your monitoring backend.

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
sync_limit = 5                     # Concurrent syncs
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

### Capacity Showing 0 Utilization
- No traffic in the measurement window
- Wait for requests to arrive, or generate test traffic
- Utilization is "best effort" estimate from recent metrics

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

### Track New Workload

Use `WorkloadTracker` in `backend/app/observability/workload.py`:

```python
from app.observability.workload import workload, JobStatus

job_id = workload.start_job("my_job", tenant_id)
# ... do work ...
workload.update_job(job_id, JobStatus.COMPLETED, records_processed=100)
```

---

## Related Documentation

- `docs/architecture.md` — System design
- `docs/development.md` — Development workflow
- `CLAUDE.md` — Code standards and layer boundaries
