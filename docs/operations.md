# Operations

Configuration reference, production deployment, and the runbook.

---

## Configuration reference

Every setting is read from the environment by `backend/app/config.py`. A `.env`
file at the repository root is loaded at startup; **a real environment variable
always wins over it**. All values have defaults that work for local development.

### Environment and auth

| Variable | Default | Purpose |
|---|---|---|
| `APP_ENV` | `development` | `production` enables the hard guards below. |
| `AUTH_SECRET` | `dev-secret-change-me` | Signs bearer tokens. **The app refuses to boot in production while this is the default** — the value is public, so a stale default would let anyone forge a token for any user and role. |

### Database

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///backend/data/platform.db` | SQLAlchemy URL. Production: `postgresql+psycopg://user:pw@host/db`. |
| `SQL_ECHO` | `0` | Log every SQL statement. Debugging only. |
| `AUTO_BOOTSTRAP` | `1` | Create the DB, migrate, and seed users on startup. **Ignored in production.** |
| `DEMO_SEED_ON_START` | `1` | Seed the demo dataset on startup. **Ignored in production, and ignored whenever `ZOHO_SOURCE=api`** — a live account means no fabricated customer should ever appear. |

### Organization

| Variable | Default | Purpose |
|---|---|---|
| `DEFAULT_ORG_ID` | `org_sanketh` | The single V1 organization. |
| `DEFAULT_ORG_NAME` | `Sanketh` | Display name. |
| `DEFAULT_CURRENCY` | `INR` | Reporting currency. |

### Zoho

| Variable | Default | Purpose |
|---|---|---|
| `ZOHO_SOURCE` | `fixture` | `fixture` (deterministic offline data) or `api` (live Zoho). See [zoho-setup.md](zoho-setup.md). |
| `ZOHO_ACCOUNTS_BASE` | `https://accounts.zoho.in` | OAuth token host. Must match the account's data centre. |
| `ZOHO_HISTORY_DAYS` | `730` | Rolling fallback window, used when no start date is chosen. |
| `ZOHO_SYNC_FROM` | — | Default start date (ISO) offered for a pull. The operator picks the actual date per run on **Data & connection**; an unparseable value falls back to the rolling window. |
| `ZOHO_PAGE_SIZE` / `ZOHO_MAX_PAGES` | `200` / `50` | Pagination bounds. |
| `ZOHO_TIMEOUT_SECONDS` | `30` | Per-request timeout. |
| `ZOHO_REQUESTS_PER_MINUTE` | `90` | Call pacing. Zoho allows ~100/min per org and a pull is one call per document, so an unpaced pull trips the limiter within seconds. `0` disables pacing. |
| `ZOHO_MAX_RETRIES` | `6` | Attempts per call before giving up. |
| `ZOHO_THROTTLE_BACKOFF_SECONDS` / `ZOHO_MAX_BACKOFF_SECONDS` | `15` / `90` | Backoff on HTTP 429, doubling and capped. Zoho's own `Retry-After` header wins when present. |
| `ZOHO_ORGANIZATION_ID` | — | Zoho Books organization id. |
| `ZOHO_CLIENT_ID` / `ZOHO_CLIENT_SECRET` / `ZOHO_REFRESH_TOKEN` | — | OAuth credentials. **Read-only scope is sufficient** — the platform never writes to Zoho. |
| `ZOHO_API_BASE` | `https://www.zohoapis.in/books/v3` | Regional API base (`.in` for India). |

### PIE (Quote Builder only)

| Variable | Default | Purpose |
|---|---|---|
| `PIE_PARSER_ROOT` | `./pie-parser` | Path to the pinned pie-parser checkout. |
| `PIE_CATALOG` | `backend/data/products.jsonl` | Decoded catalogue path. |
| `AUTO_BUILD_CATALOG` | `1` | Build the catalogue lazily if missing. Set `0` in constrained deploys and build out of band. |
| `PIE_WARM` | `1` | Warm the engine at startup. `0` starts much faster. |
| `PIE_TOP_N` | `6` | Max ranked alternatives per quote line. |

### AI Decision Layer

| Variable | Default | Purpose |
|---|---|---|
| `AI_PROVIDER` | `mock` | `mock` (deterministic, offline) or `anthropic` (live). |
| `AI_MODEL` | `claude-haiku-4-5-20251001` | Model id. Interpretation is a small, bounded task — a fast model suits it. |
| `AI_MAX_TOKENS` | `400` | Output cap. |
| `AI_TIMEOUT_SECONDS` | `20` | Per-call timeout; on expiry the decision degrades to FAILED. |
| `ANTHROPIC_API_KEY` | — | Required when `AI_PROVIDER=anthropic`. |
| `ANTHROPIC_API_BASE` | `https://api.anthropic.com` | Override for a gateway/proxy. |
| `PROMPT_VERSION` | `p1` | Stamped on every decision for provenance. |
| `PRIORITY_HIGH_AT` / `PRIORITY_MEDIUM_AT` | `70` / `40` | Priority band cutoffs. |
| `AI_PRIORITY_ADJUST_BOUND` | `20` | Hard clamp on the AI's priority influence. |

### AI observability

| Variable | Default | Purpose |
|---|---|---|
| `AI_TELEMETRY_ENABLED` | `1` | Write one `ai_call_logs` row per interpretation. |
| `AI_COST_PER_MTOK_INPUT` | `1.0` | USD per million input tokens. **Set to your actual contracted rate** — every cost figure derives from this; the default is indicative only. |
| `AI_COST_PER_MTOK_OUTPUT` | `5.0` | USD per million output tokens. Same caveat. |
| `AI_DEGRADED_RATE_MAX` | `0.25` | Upper health bound on the degraded rate. |
| `AI_DEGRADED_RATE_MIN` | `0.005` | Lower bound — a gate that never rejects is as suspicious as one that always does. |
| `AI_HEALTH_MIN_SAMPLE` | `20` | Minimum calls before any health inference is drawn. |

### Provenance

| Variable | Default | Purpose |
|---|---|---|
| `DETECTOR_VERSION` | `v0` | Stamped on every signal, for reproducibility. |

---

## Production deployment

### What `APP_ENV=production` changes

1. **Boot guard** — the app refuses to start while `AUTH_SECRET` is the
   development default.
2. **No auto-migration** — schema changes become a deliberate, reviewed deploy
   step. `AUTO_BOOTSTRAP` is ignored.
3. **No demo data** — `DEMO_SEED_ON_START` is ignored, and
   `POST /api/v1/internal/demo-seed` returns 403. Fabricated customers must
   never reach a real read model.

### Deploy sequence

```bash
export APP_ENV=production
export AUTH_SECRET="$(openssl rand -base64 32)"
export DATABASE_URL="postgresql+psycopg://user:pw@host:5432/decision_platform"
export AI_PROVIDER=anthropic
export ANTHROPIC_API_KEY="sk-ant-..."
export AI_COST_PER_MTOK_INPUT=... AI_COST_PER_MTOK_OUTPUT=...   # your real rates

python -m pip install -r backend/requirements.txt

cd backend
python -m alembic upgrade head     # schema — explicit, reviewed
python -m app.seed                 # org + users only; no demo data

python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Build and serve the frontend as static files:

```bash
cd frontend && npm ci && npm run build     # → frontend/dist/
```

### Before going live — known gates

These are deployment decisions, not code defects, and the platform is **not
ready for real customer data** until they are closed:

1. **Replace the demo login.** `routers/platform_auth.py` accepts any password
   for a known email. It is explicitly demo-grade auth and must be swapped for
   the organization's identity provider.
2. **Give every salesperson a Zoho account with a matching email.** The sync now
   maps the salesperson on a customer's most recent invoice to a platform user,
   but only on an exact email match. Salesperson scope is
   `assigned_user_id == user_id`, so any account it cannot map stays invisible
   to the person who should act on it. After the first live sync, check
   **Skipped rows** on **Data & connection** for `UNMAPPED_SALESPERSON` and
   `ASSIGNMENT_UNAVAILABLE` (the latter means the `ZohoBooks.users.READ` scope
   is missing) — and check that invoices in Zoho actually carry a salesperson.
3. **Set the real AI cost rates** (above), or every cost figure is wrong.

Also worth knowing: the **Outcome Tracker is not built**. You can measure
adoption and decision quality today, but not the realised monetary impact of
accepted recommendations.

---

## Runbook

### Health

```bash
curl http://localhost:8000/api/v1/internal/health
```

Returns database connectivity and the configured Zoho source. It does **not**
check the AI provider — use the metrics endpoint for that.

### AI cost and health (owner only)

```bash
curl -H "Authorization: Bearer $OWNER_TOKEN" \
     http://localhost:8000/api/v1/internal/ai-metrics
```

Rolling 7- and 30-day windows: call counts by status, degraded/failed/
suppressed/cache-hit rates, cost per decision and per day, latency, and the
failure-reason distribution.

**Reading the health band:**

| Band | Meaning | Action |
|---|---|---|
| `OK` | Degraded rate within the expected band. | None. |
| `HIGH` | The gate is rejecting a lot. | Investigate the prompt, the model, or the fact sets being sent. Check `failure_reasons`: `SCHEMA_INVALID` is a schema/prompt-format problem; `UNGROUNDED_NUMBER` / `SCALE_VIOLATION` are grounding problems. |
| `SUSPICIOUSLY_LOW` | The gate is rejecting almost nothing. | Either it is too permissive, or the prompt is so constrained the model is adding no interpretive value. Both are worth knowing. |
| `INSUFFICIENT_DATA` | Below `AI_HEALTH_MIN_SAMPLE` calls. | Wait for volume; no inference is drawn. |

### Operational endpoints

All require manager or owner; `ai-metrics` requires owner.

| Endpoint | Purpose |
|---|---|
| `POST /api/v1/internal/sync/zoho` | Pull Zoho into the read model. Idempotent; reports what was written and what was skipped and why. |
| `POST /api/v1/data/sync` | The same pull plus detectors and decision generation, recorded as a `SyncRun`. Optional body `{"since": "2025-01-01", "full": false}` — `since` sets the start date, `full` discards the resume cursor. This is what the **Data & connection** screen calls. |
| `POST /api/v1/internal/detectors/run` | Run the deterministic Signal Engine. No AI. |
| `POST /api/v1/internal/decisions/generate` | Turn the latest signals into decisions via the AI layer. |
| `GET /api/v1/internal/ai-metrics` | AI cost and health (owner only). |
| `POST /api/v1/internal/demo-seed` | Demo data. **403 in production.** |

### Normal operating cycle

```
sync/zoho  →  detectors/run  →  decisions/generate
```

Schedule it (cron, or your scheduler of choice) at whatever cadence suits the
business — daily is reasonable for this data. Each step is idempotent, and
`decisions/generate` skips re-inference when a decision's underlying context is
unchanged, so re-running it costs nothing extra.

The pull is **resumable**: every invoice and bill is recorded once its lines are
written, so a run cut short by Zoho's rate limiter is continued rather than
repeated by the next one. That run is recorded as `PARTIAL`, not `FAILED` — it
carries the counts it actually wrote, because a run that wrote 336 sales lines
and then stopped did write 336 sales lines, and recording zero would leave the
audit trail contradicting the database.

### Backup and recovery

The database holds two very different kinds of data:

| Kind | Recovery |
|---|---|
| **Read model** (customers, products, sales, costs) | A rebuildable projection of Zoho. Recover by re-running the sync. |
| **Platform state** (signals, decisions, human actions, telemetry) | **Exists nowhere else.** The decision audit trail cannot be reconstructed. Back it up. |

Standard `pg_dump` on the Postgres database covers both. Restore, then re-run
the sync to bring the read model current.

### Cost control

Three mechanisms are already in place, in order of impact:

1. **Context-hash caching** — an unchanged decision context is never re-sent to
   the model. Visible as `cache_hit` in the metrics.
2. **Up-front suppression** — insufficient evidence withholds the recommendation
   without spending a call.
3. **Bounded output** — `AI_MAX_TOKENS`, temperature 0.

If cost is still too high, lower the run cadence before reaching for a smaller
model — most spend is call volume, not tokens per call.
