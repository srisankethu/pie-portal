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
| `CREDENTIAL_ENCRYPTION_KEY` | *(fixed dev key)* | Encrypts every organization's Zoho client secret and refresh token at rest (see `app/crypto.py`). **The app refuses to boot in production while this is the default** — same reasoning as `AUTH_SECRET`: the value is public. Generate one with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Rotating it makes every stored connection undecryptable — reconnect them afterward. |

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
| `DEFAULT_ORG_ID` | `org_sanketh` | The organization seeded automatically at bootstrap. Every other one is provisioned explicitly — see `app/provision_org.py` and [zoho-setup.md](zoho-setup.md#multiple-organizations). Each organization is a fully separate tenant: its own users, its own Zoho connection, its own decisions. |
| `DEFAULT_ORG_NAME` | `Sanketh` | Display name for the default organization. |
| `DEFAULT_CURRENCY` | `INR` | Reporting currency. |

### Zoho

Only `ZOHO_SOURCE` and the pull-behaviour settings below are process-wide. Every
organization's actual credentials (organization id, client id/secret, refresh
token, data-centre hosts) live per-organization in the database, encrypted —
connected via `PUT /api/v1/data/connection`, not an environment variable (see
[zoho-setup.md](zoho-setup.md)). The `ZOHO_ORGANIZATION_ID` /
`ZOHO_CLIENT_ID` / `ZOHO_CLIENT_SECRET` / `ZOHO_REFRESH_TOKEN` /
`ZOHO_ACCOUNTS_BASE` / `ZOHO_API_BASE` variables still exist, but only as the
fallback for `DEFAULT_ORG_ID` when it has no stored connection of its own — the
pre-multi-tenant configuration path, kept so an existing single-tenant
deployment needs no migration step.

| Variable | Default | Purpose |
|---|---|---|
| `ZOHO_SOURCE` | `fixture` | `fixture` (deterministic offline data) or `api` (live Zoho), for every organization alike. |
| `ZOHO_HISTORY_DAYS` | `730` | Rolling fallback window, used when no start date is chosen. Shared across every organization. |
| `ZOHO_SYNC_FROM` | — | Default start date (ISO) offered for a pull. The operator picks the actual date per run on **Data & connection**; an unparseable value falls back to the rolling window. |
| `ZOHO_PAGE_SIZE` / `ZOHO_MAX_PAGES` | `200` / `50` | Pagination bounds. |
| `ZOHO_TIMEOUT_SECONDS` | `30` | Per-request timeout. |
| `ZOHO_REQUESTS_PER_MINUTE` | `90` | Call pacing. Zoho allows ~100/min per org and a pull is one call per document, so an unpaced pull trips the limiter within seconds. `0` disables pacing. |
| `ZOHO_MAX_RETRIES` | `6` | Attempts per call before giving up. |
| `ZOHO_THROTTLE_BACKOFF_SECONDS` / `ZOHO_MAX_BACKOFF_SECONDS` | `15` / `90` | Backoff on HTTP 429, doubling and capped. Zoho's own `Retry-After` header wins when present. |
| `ZOHO_ORGANIZATION_ID` | — | *Default-organization fallback only.* Zoho Books organization id. |
| `ZOHO_CLIENT_ID` / `ZOHO_CLIENT_SECRET` / `ZOHO_REFRESH_TOKEN` | — | *Default-organization fallback only.* OAuth credentials. **Read-only scope is sufficient** — the platform never writes to Zoho. |
| `ZOHO_ACCOUNTS_BASE` | `https://accounts.zoho.in` | *Default-organization fallback only.* OAuth token host — must match the account's data centre. |
| `ZOHO_API_BASE` | `https://www.zohoapis.in/books/v3` | *Default-organization fallback only.* Regional API base (`.in` for India). |

### PIE (Quote Builder only)

| Variable | Default | Purpose |
|---|---|---|
| `PIE_PARSER_ROOT` | `./pie-parser` | Path to the pie-parser submodule (or your own checkout). |
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

Packaged, with Postgres and TLS already wired together:
**[hosting.md](hosting.md)** — `make deploy-build`, `make deploy-release`,
`make deploy-up`. Prefer it. What follows is the same sequence by hand, for a
deployment that supplies its own process manager and reverse proxy.

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

1. **Move sign-in onto the organization's identity provider.** This gate has
   partly closed and the rest of it is unchanged. `routers/platform_auth.py` no
   longer accepts any password for a known email — it verifies a PBKDF2 hash, a
   user row without one cannot sign in at all, and every failure returns one
   indistinguishable 401. What is still missing is SSO, MFA, per-person
   provisioning, and a password reset that does not go through an operator
   running `python -m app.seed --set-password`.
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

## Scheduling the sync

**Nothing in the application schedules a pull.** `POST /api/v1/data/sync` is
started by a person pressing the button on Data & connection, or by something
outside this repo calling it. Until a timer exists, every screen shows whatever
the last manual sync left behind — and the morning read says so, in as many
words, at the top of the landing page.

`scripts/scheduled_sync.py` is that something. It signs in, calls the same
endpoint the screen calls, waits for the run it started, and exits non-zero if
it failed — which is the whole interface, because a non-zero exit is what makes
cron mail you.

```bash
cd backend && python3 ../scripts/scheduled_sync.py
```

| Variable | |
|---|---|
| `PIE_BASE_URL` | where the API is (default `http://localhost:8000`) |
| `PIE_SYNC_EMAIL` | an owner or manager account — a salesperson is refused |
| `PIE_SYNC_PASSWORD` | that account's password |
| `PIE_SYNC_TIMEOUT` | seconds to wait for the pull (default 3600; `0` starts it and returns) |

| Exit | Meaning |
|---|---|
| 0 | finished, or a pull was already running |
| 1 | configuration or network problem — nothing was started |
| 2 | the sync ran and failed, or did not finish in time |

**Give it its own account** rather than a person's. A password change should not
silently stop the nightly pull, and the sync's activity should be attributable
to the sync. Any owner or manager works; a salesperson is refused with a 403 the
script names.

**It signs in on every run rather than carrying a token.** Tokens here have no
expiry — `verify_token` checks the signature and never reads `iat` — so a token
in a crontab is an unexpiring credential with an owner's authority, and
withdrawing it means rotating `AUTH_SECRET` and signing every user out. A
password can be changed for one account without touching anyone else.

### crontab

Pick the hour to suit the business: the pull should land before the first person
looks, and after the day's invoicing is done in Zoho. The environment belongs in
a file only root can read, not in the crontab line.

```cron
# /etc/cron.d/pie-sync   — replace HH:MM with your time
MM HH * * *  pie  set -a; . /etc/pie/sync.env; set +a; cd /srv/pie/backend && /usr/bin/python3 ../scripts/scheduled_sync.py
```

```bash
# /etc/pie/sync.env   — chmod 600, owned by the user cron runs as
PIE_BASE_URL=http://localhost:8000
PIE_SYNC_EMAIL=sync@yourdomain
PIE_SYNC_PASSWORD=...
```

Cron mails the job's output to the crontab's owner, so set `MAILTO` — a job
nobody hears from is a job nobody notices has stopped.

### systemd timer

Preferable where it is available: `systemctl list-timers` answers "did it run"
without reading a log, and a missed run while the machine was off is caught by
`Persistent=true`.

```ini
# /etc/systemd/system/pie-sync.service
[Service]
Type=oneshot
User=pie
EnvironmentFile=/etc/pie/sync.env
WorkingDirectory=/srv/pie/backend
ExecStart=/usr/bin/python3 ../scripts/scheduled_sync.py
```

```ini
# /etc/systemd/system/pie-sync.timer     — replace HH:MM with your time
[Timer]
OnCalendar=*-*-* HH:MM:00
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
systemctl enable --now pie-sync.timer
systemctl list-timers pie-sync.timer     # when it next fires, when it last did
journalctl -u pie-sync.service -n 50     # what it said
```

### Checking it is working

The morning read is the check that matters, because it is the one somebody
already looks at: a green "Synced N hours ago" strip means the timer is running,
and an amber "this is not today's picture" means it is not. `GET /api/v1/data/sync`
answers the same question for a monitor, in `last.status` and `last.finished_at`.

A run firing while the previous one is still going does **not** start a second
pull and does **not** fail — the endpoint returns the job already in flight and
the script reports it and exits 0. That is ordinary during a first sync reading
years of documents, and mailing about it nightly would teach people to filter
the mail that also carries the real failures.

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

Manager or owner unless noted; `PUT`/`DELETE /data/connection` and
`ai-metrics` are owner only — a connection is a write credential for the whole
organization's commercial data, not an operational action.

| Endpoint | Purpose |
|---|---|
| `POST /api/v1/internal/sync/zoho` | Pull Zoho into the read model, using the caller's own organization's connection. Idempotent; reports what was written and what was skipped and why. |
| `POST /api/v1/data/sync` | The same pull plus detectors and decision generation, recorded as a `SyncRun`. Optional body `{"since": "2025-01-01", "full": false}` — `since` sets the start date, `full` discards the resume cursor. This is what the **Data & connection** screen calls. |
| `PUT /api/v1/data/connection` | **Owner only.** Connect (or replace) the caller's own organization's Zoho account. Body: `zoho_organization_id`, `client_id`, `client_secret`, `refresh_token`, optional `accounts_base`/`api_base`. Secrets are encrypted before storage and never echoed back. |
| `DELETE /api/v1/data/connection` | **Owner only.** Unlink the caller's own organization's Zoho connection. Read-model data already pulled is untouched. |
| `GET /api/v1/internal/zoho/check` | Verify the caller's own organization's connection without pulling data. |
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

---

## Continuous integration

Two workflows, and they answer different questions.

### `gate.yml` — does this change hold up?

Runs on every pull request and every push to `main`, and blocks: everything it
checks is under this repository's control. It calls `./scripts/verify.sh`
rather than restating the steps, so `make verify` on a laptop and the gate in CI
are the same list — see the header of that script for why that matters.

The frontend step inside `verify.sh` runs `npm test` before `tsc -b` and
`vite build`. Until those tests existed the build *was* the entire frontend
gate, which meant a screen could render the wrong number and pass as long as the
types lined up. `LineGrid.test.tsx` is the one to keep: it renders the quote grid
for a sales role from a fixture deliberately carrying cost and margin and asserts
neither appears. The server omitting them is the real guarantee and is tested in
the backend suite; this covers the other way it could break.

The `pie-contract` job is the only one needing a credential, and it is separate
so that an expired token fails it alone instead of taking the gate with it. See
`backend/tests/conftest.py` for the `requires_pie` marker that lets the other
~1,150 tests run with no engine checked out.

### `live.yml` — has the world moved?

Runs weekly (Mondays, 04:00 UTC / 09:30 IST) and on demand via
**Actions → live contracts → Run workflow**, where the `suite` input selects
`ai`, `zoho` or both.

It exercises `backend/tests/live/`, which the default suite deliberately
excludes (`pytest.ini` carries `addopts = -m "not live"`) because these tests
call a real model and a real Zoho book. That exclusion was right and the suites
still ended up never running anywhere, which is why this workflow exists.

It fails loudly when a contract breaks — a red scheduled workflow emails the
repository owner, and that notification is the only channel a weekly check has.
It still cannot block anyone: there is no `pull_request` trigger, so it never
appears as a check on a PR. A failure here means the world moved, not that a
commit is broken.

Note that a `schedule:` trigger only fires from the **default branch**, so the
weekly run begins only once this is on `main`.

| Secret | Enables |
|---|---|
| `ANTHROPIC_API_KEY` | The AI provider contract suite — that the grounding gate still refuses ungrounded figures and injected instructions when pointed at a real model rather than the offline mock. |
| `ZOHO_ORGANIZATION_ID`, `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`, `ZOHO_REFRESH_TOKEN` | The Zoho contract suite — that the live API still returns the fields the client maps. Read-only scope is sufficient; the suite asserts that no non-GET ever reaches the API host. |

Set `ZOHO_ACCOUNTS_BASE` and `ZOHO_API_BASE` as repository **variables** (not
secrets) if the account is outside the `.in` data centre.

Each suite skips itself, with its reason printed by `-ra`, when its credentials
are absent — so configuring one and not the other still runs the one.

Locally: `make test-live`.
