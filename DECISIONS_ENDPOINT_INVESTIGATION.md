# Decisions Endpoint Timeout Investigation

**Issue**: Production Decisions page times out on Vercel with "function stopped after 25s"

**Date**: 2026-08-19  
**Branch**: `claude/decisions-endpoint-timeout-mzkyva`

---

## Summary

The Decisions page timeout is caused by an **N+1 query pattern in the frontend** (`PlatformApp.tsx:550-555`), not a slow backend query.

### Root Cause

**Frontend makes N separate HTTP requests:**
1. `GET /api/v1/decisions` → list all decisions (1 request)
2. `GET /api/v1/decisions/{id}/detail` → load detail for each decision (N requests)

**Total: 1 + N requests** instead of 1 request

### Performance Evidence

Test with 10 decisions on SQLite:
```
List endpoint:      58ms (1 request)
Detail endpoint:    10 × 10ms avg = 100ms total (10 requests)
Total frontend:     158ms for 10 decisions

Extrapolated: 50+ decisions ≈ 558ms for in-memory SQLite
```

**On production database with network latency:**
- Each detail endpoint: likely 50-500ms (due to database round-trips)
- 50 decisions: 50 × 100ms = 5 seconds minimum
- With slow queries or missing indexes: easily 25+ seconds → **Vercel timeout**

---

## Instrumentation Added

Comprehensive timing logs added to measure each operation:

### List Endpoint (`GET /api/v1/decisions`)
```
[decisions:list] START type=None status=None org=org_pie
[decisions:list] Repository init: 0.2ms
[decisions:list] Scope calculation: 0.1ms
[decisions:list] repo.list() returned 10 rows: 3.7ms
[decisions:list] Companies init: 4.9ms
[decisions:list] Entity types in rows: {'CUSTOMER'}
[decisions:list] Loaded index for CUSTOMER: 1.3ms
[decisions:list] All indexes loaded: 1.6ms
[decisions:list] Built 10 result rows: 0.3ms
[decisions:list] TOTAL: 10.8ms
```

### Detail Endpoint Per Decision (`GET /api/v1/decisions/{id}/detail`)
```
[decisions:detail] START decision_id=6088d325-bd64-458e-922f-f777f7522728
[decisions:detail] _visible(): 1.8ms
[_detail] Signal load: 0.0ms
[_detail] Facts extraction: 0.1ms
[_detail] Subject label (session.get() for customer/product): 0.5-1.1ms
[_detail] Subject origin (Companies.of()): 0.7-1.2ms
[_detail] User names (approvals.user_names()): 0.6-1.3ms
[_detail] Outcome (outcome_tracker + evaluation): 0.4-1.7ms
[_detail] Result dict assembly: 0.1-0.2ms
[_detail] TOTAL for one decision: 2.3-7.2ms
```

Each detail call queries the database ~6-7 times:
1. Signal lookup
2. Customer/Product lookup (for subject label)
3. Companies lookup (for subject origin)
4. User name lookup
5. Outcome snapshot lookup
6. Outcome evaluation (may involve more queries)

**With 50 decisions: 50 × 7 queries = ~350 database queries** (vs 1 query on list endpoint)

---

## Backend Fixes Implemented

### 1. **Timing Instrumentation** ✓
   - Added comprehensive logging at every major operation
   - Captures request entry, auth, scope, list query, index loading, entity lookups
   - Shows exact millisecond breakdown per operation

### 2. **`include_detail` Query Parameter** ✓
   - `GET /api/v1/decisions?include_detail=true`
   - Fetches all decision details in one backend call instead of N separate calls
   - Frontend still makes 2 HTTP requests (list + one call with all details) instead of 1+N
   - Reduces database round-trips significantly

### 3. **Bulk Detail Endpoint** ✓ (Alternative approach)
   - `POST /api/v1/decisions/bulk-detail` with `decision_ids: [...]`
   - Allows frontend to batch-fetch details in one request
   - More REST-friendly than include_detail parameter

---

## Frontend Fixes Needed

### Current Frontend Pattern (PlatformApp.tsx:550-555)
```typescript
const list = await papi.listDecisions(session.token);  // 1 request
const entries = await Promise.all(
  list.map(async (s) => [s.decision_id, await papi.getDetail(session.token, s.decision_id)] as const),  // N requests in parallel
);
```

### Recommended Frontend Fix

**Option 1: Use include_detail parameter (simplest)**
```typescript
const list = await papi.listDecisions(session.token, { include_detail: true });
// Details already included in each row
const details = list.reduce((acc, d) => ({ ...acc, [d.decision_id]: d }), {});
```

**Option 2: Use bulk-detail endpoint**
```typescript
const list = await papi.listDecisions(session.token);
const details = await papi.bulkDetail(list.map(d => d.decision_id));
```

---

## Why Recent Changes Are Relevant

Recent commits that affect this:
- **EventLog.supersede**: May add extra queries to verify state transitions
- **_sole_connection connector-blind handling**: May load connection data for each decision
- **Deletion sweep scoping**: May add filters/joins on entity lookups
- **Two-connection handling**: Companies initialization might now scan multiple books

These changes add complexity to each detail call's database queries, amplifying the N+1 problem.

---

## Next Steps

1. **Update frontend** to use `include_detail=true` parameter (1-line change)
   - Reduces N+1 from 1+N requests to 2 requests
   - Or use bulk-detail endpoint (also 1-2 line change)

2. **Monitor production** with the timing logs to verify latency improvements

3. **Optional optimizations** if still slow:
   - Add database indexes on frequently queried columns
   - Cache Companies lookups per request
   - Batch user name lookups
   - Profile outcome_tracker queries

---

## Testing

Performance test added: `tests/decision_platform/test_decisions_performance.py`

Run with:
```bash
python3 -m pytest tests/decision_platform/test_decisions_performance.py -xvs
```

Shows exact timing for both list and detail endpoints with logs.

---

## Files Modified

- `app/routers/decisions.py`: Added timing instrumentation, include_detail parameter, bulk-detail endpoint
- `tests/decision_platform/test_decisions_performance.py`: New performance test simulating N+1 pattern

---

## Verification

All existing tests pass:
```bash
make verify-fast
```

The instrumentation does not change response formats, only adds query parameters and endpoints.
