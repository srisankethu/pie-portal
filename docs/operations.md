# Operations

Configuration reference, production deployment, and the runbook.

Non-Zoho ERP connections (NetSuite, Business Central, Acumatica, Prophet 21,
Sage) need **no environment configuration**: their credentials are entered
per organization in Data & connection and stored encrypted. Setup per system
is in `docs/connectors.md`. The `ZOHO_*` variables below configure the Zoho
client and the legacy single-tenant fallback only; `ZOHO_SOURCE=api` remains
the process-wide switch that turns every connector's live client on.

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
| `LOG_LEVEL` | `INFO` | Root log level (`DEBUG`/`INFO`/`WARNING`/`ERROR`). One place configures logging for the API, the sync CLI and bootstrap alike — `app/observability/logs.py`. |
| `LOG_FILE` | *(empty)* | Also write the log to this file, rotating. Empty means stdout only, which is right where the platform captures stdout and wrong where nothing does. |
| `LOG_FILE_MAX_BYTES` | `10485760` | Rotate at this size. |
| `LOG_FILE_KEEP` | `5` | How many rotated files to keep, so logs from March cannot fill the disk. |
| `SYNC_LOG_MAX_LINES` | `5000` | How many lines of one sync's log are kept in the database for the Data screen. Warnings and errors are never dropped by this cap, and a log that hits it says so in its own last lines. |
| `CREDENTIAL_ENCRYPTION_KEY` | *(fixed dev key)* | Encrypts every organization's Zoho client secret and refresh token at rest (see `app/crypto.py`). **The app refuses to boot in production while this is the default** — same reasoning as `AUTH_SECRET`: the value is public. Generate one with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Rotating it makes every stored connection undecryptable — reconnect them afterward. It also unreads every organization's data key, and reconnecting does **not** fix that half: see [the runbook entry](#could-not-unwrap-this-organizations-data-key) before rotating. |

### Database

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///backend/data/platform.db` | SQLAlchemy URL. Production: `postgresql+psycopg://user:pw@host/db` — see [postgres.md](postgres.md), including the data-move tool for an existing SQLite file. |
| `SQL_ECHO` | `0` | Log every SQL statement. Debugging only. |
| `DB_POOL_SIZE` | `5` | Postgres pool per process (ignored on SQLite). The sizing arithmetic is on the setting in `config.py`; redo it before raising. |
| `DB_MAX_OVERFLOW` | `10` | Extra Postgres connections under burst, released when idle. |
| `DB_POOL_TIMEOUT` | `30` | Seconds a request waits for a free connection before failing loudly. |
| `DB_POOL_RECYCLE` | `1800` | Retire pooled connections before proxy/NAT idle cutoffs drop them first. |
| `DB_SLOW_QUERY_MS` | `1000` | Log statements slower than this (0 = off; the compose stack sets 500). Statement text only — parameter values never reach the log. |
| `REDIS_URL` | *(empty)* | Provisioned infrastructure (both compose stacks run one); no feature requires it yet, and nothing may refuse to serve because it is absent. |
| `AUTO_BOOTSTRAP` | `1` | Create the DB, migrate, and seed users on startup. **Ignored in production.** |
| `DEMO_SEED_ON_START` | `1` | Seed the demo dataset on startup. **Ignored in production, and ignored whenever `ZOHO_SOURCE=api`** — a live account means no fabricated customer should ever appear. |

### Organization

| Variable | Default | Purpose |
|---|---|---|
| `DEFAULT_ORG_ID` | `org_pie` | The organization seeded automatically at bootstrap. Every other one is provisioned explicitly — see `app/provision_org.py` and [zoho-setup.md](zoho-setup.md#multiple-organizations). Each organization is a fully separate tenant: its own users, its own Zoho connection, its own decisions. |
| `DEFAULT_ORG_NAME` | `PIE` | Display name for the default organization. |
| `DEFAULT_CURRENCY` | `INR` | Reporting currency. |

### Plans and sign-up

| Variable | Default | Purpose |
|---|---|---|
| `DEFAULT_PLAN` | `platform` | The plan an organization is on when its own row does not say. Defaults to the widest so an existing single-tenant deployment keeps every feature it has; a hosted deployment sets `free` and upgrades explicitly with `python -m app.entitlements set-plan`. An unrecognised value resolves to `free` and logs. |
| `INTELLIGENCE_TRIAL_DAYS` | `30` | Length of the free Commercial Intelligence month. Keyed to the **connected Zoho books**, not to the platform organization, so a second sign-up with a second address does not buy a second trial. |
| `SELF_SERVE_SIGNUP` | `0` | Whether anyone who can reach this deployment may create a tenant for themselves (`POST /api/v1/signup`). **Off by default, deliberately** — it is the only unauthenticated endpoint here that writes, so an existing install that pulls new code must not silently start accepting strangers. Turning it on also makes the landing page's "Get started free" lead to a sign-up form instead of the sign-in card. |
| `SIGNUP_RATE_LIMIT_PER_HOUR` | `5` | Sign-ups accepted per client address per hour. A speed bump, not a control: the counter is in one process's memory, does not survive a restart, is not shared between workers, and behind the reverse proxy in `deploy/` it sees the proxy rather than the client — so it limits globally there. What it buys is that hashing a password (240,000 PBKDF2 rounds, by design) cannot be used as a CPU amplifier. `0` disables it. Put a real limiter in front of the app if you expect real abuse. |

A self-serve sign-up lands on **free**, whatever `DEFAULT_PLAN` says — pinned in
`onboarding.SIGNUP_PLAN`, because `DEFAULT_PLAN` defaults to `platform` and
inheriting it would hand the top tier to anyone who can reach the form. There
is deliberately no API that changes a plan; that stays an operator command.

**The sign-up form asks which plan a business wants, and the answer grants
nothing.** It is stored on `organizations.requested_plan`, a column no
resolution path reads — `licensed_plan` still reads `plan` alone — so the
picker cannot become the plan-setting API that does not exist. What it buys is
that the question has an answer somebody can find:

```bash
python -m app.entitlements requests                  # who is asking for more
python -m app.entitlements set-plan <org_id> intelligence   # the only thing that grants it
```

The form says as much where it is asked: an account starts on the free Quote
Desk the same day whichever plan is selected, and nothing is charged at sign-up.
There is no billing in this product.

**Turning sign-up on is what makes registration reachable at all.** With
`SELF_SERVE_SIGNUP=0` the sign-in card offers no way to create an organization,
and that is correct for a single-tenant install — the only accounts are the ones
an owner creates from Settings. With it on, the card carries a "Create your
organization" link and the landing page's pricing panels open the sign-up form
on the plan that was being read about.

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
| `PROMPT_VERSION` | `p2` | Stamped on every decision and every telemetry row for provenance. Bump it when the system prompt changes. |
| `PRIORITY_HIGH_AT` / `PRIORITY_MEDIUM_AT` | `70` / `40` | Priority band cutoffs. |
| `AI_PRIORITY_ADJUST_BOUND` | `20` | Hard clamp on the AI's priority influence. |

### Turning the AI on

The shipped default is **`AI_PROVIDER=mock`** — an offline stand-in that
restates the signal's own figures and labels itself as a stand-in on every
card. Nothing is sent anywhere and nothing is charged. The deterministic engine
is unaffected either way: it computes every number on every screen, with or
without a model.

To run the narrative layer on a real model:

1. **Set two variables and restart.**

   ```bash
   export AI_PROVIDER=anthropic
   export ANTHROPIC_API_KEY="sk-ant-..."
   ```

   Set `AI_COST_PER_MTOK_INPUT` / `_OUTPUT` to your actual contracted rates at
   the same time. Every cost figure in the product derives from them, and the
   defaults are indicative only.

2. **Check the screen before the bill.** Sign in as the owner →
   **Settings → AI layer**. It says which provider is *really* running (a
   configured provider that cannot be built falls back to the mock rather than
   failing the screen — the panel names that case explicitly), how many provider
   calls the next decision run would make, and what it would cost at your rates.
   It calls nothing to work that out.

3. **Generate decisions and read them.** `POST /api/v1/internal/decisions/generate`,
   or the button on the decisions screen. Then look at the cards: a live reading
   names the account, quotes the figures it was given, and proposes something
   specific. If several cards read alike, the AI is not earning its place and
   the honest response is to turn it back off.

4. **Watch the same panel afterwards.** Spend over seven days, median latency,
   and the share of readings the validation gate refused. A refused reading
   still surfaces the decision with the deterministic sentence, so a bad model
   costs money and clarity, never correctness.

**What the model can and cannot do, mechanically.** It receives the curated
fact bundle and nothing else — no raw records, no customer names (those are
pseudonymised on the way out and restored on the way back), and no cost or
margin for a salesperson recipient. On the way out, every number in its text
must trace to a supplied fact or to the policy lines it was shown; a surfaced
reading that quotes no figure at all is refused too. Anything refused degrades
to the deterministic template. `backend/tests/live/test_live_provider_contract.py`
asserts all of this against a real model — `make test-live`, which skips itself
cleanly when no key is set.

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
| `PIE_SYNC_FULL` | `1` to discard the resume cursor and re-read every document in the window |
| `PIE_SYNC_SINCE` | `YYYY-MM-DD` to override how far back to read; the server's `ZOHO_SYNC_FROM` applies when unset |

`--full` and `--since YYYY-MM-DD` do the same two things from a terminal,
without editing the timer's environment and putting it back.

### Incremental nightly, full weekly

**A full pull is not the nightly one.** Incremental stops listing at the
high-water mark, so a nightly run over two years of history costs a handful of
calls instead of thousands. A full pull re-reads everything in the window —
minutes to hours against a real book — and Zoho's rate limit is a shared daily
budget, so running one every night spends the allowance the day's real work
needs.

What it is for is *repair*: a document that changed in a way its
`last_modified_time` did not record, or history skipped by a bug since fixed
that has to be read again to come back.

Two lines, so the schedule stays in cron where you can see it rather than in
the script:

```cron
# nightly, incremental — replace HH:MM
MM HH * * 1-6  pie  set -a; . /etc/pie/sync.env; set +a; cd /srv/pie/backend && /usr/bin/python3 ../scripts/scheduled_sync.py

# weekly, full. Give it a longer ceiling: the default hour is generous for an
# incremental pull and can be short for a full one on a large book.
MM HH * * 0    pie  set -a; . /etc/pie/sync.env; set +a; PIE_SYNC_FULL=1 PIE_SYNC_TIMEOUT=21600 cd /srv/pie/backend && /usr/bin/python3 ../scripts/scheduled_sync.py
```

### Recovering history after a fix

When a bug caused documents to be read wrongly, an incremental pull will not
bring them back: nothing about them changed in Zoho, so the high-water mark
skips them. Re-read the affected period once, by hand:

```bash
cd backend && python3 ../scripts/scheduled_sync.py --full --since 2024-04-01
```

It prints which mode it is running before it starts, because the first question
about a job that has been going for an hour is always which one it is:

```
requesting a full sync from 2024-04-01
started sync 3d9552e8-…
sync 3d9552e8-… finished OK — customers 3, products 2, sales_txns 87, …
```

Pick `--since` to cover the period in question, not the whole history —
re-reading four years to repair four months is the same answer for more money.

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

### Where the logs are

Three places, and they answer different questions.

**One sync, from the screen.** Data & connection → *What this sync did*. Every
phase the pull entered, every warning, and the full traceback if it failed —
kept with the run, so a sync that stopped an hour in can still be read
afterwards. "Problems only" filters to warnings and errors; *Download log*
gives the whole thing as a text file. Manager or owner only, because a log line
is whatever the code passed to it and the cost-record stage names purchase
documents.

Also on the API, if you would rather curl it:

```bash
curl -sH "Authorization: Bearer $TOKEN" \
  "$BASE/api/v1/data/sync-runs/$RUN_ID/log?problems_only=true" | python3 -m json.tool
curl -sH "Authorization: Bearer $TOKEN" "$BASE/api/v1/data/sync-runs/$RUN_ID/log.txt"
```

**The process log.** stdout, and a rotating file when `LOG_FILE` names one. On
a platform that captures stdout (Railway, Fly, a systemd unit with journald)
that is where everything lands; set `LOG_FILE` on anything with a disk and
nothing collecting stdout, or the log lives as long as the terminal does.

**A run's counters and worklist.** The sync card and *What could not be
resolved* on the same screen — what landed, and what to fix. The log explains;
those two say what happened to the data.

> **Migrations used to switch the application log off.** `alembic/env.py` calls
> `logging.config.fileConfig`, which replaces the root handlers, forces the root
> level to `WARN`, and (defaulting to `disable_existing_loggers=True`) disables
> every logger that already exists — every `pie_portal.*` one. The app runs
> migrations *in-process* at startup, so a deployment came up with no
> application logging at all: no INFO, and no traceback from a failing sync.
> Fixed by leaving logging alone when the app has already configured it. If you
> add anything that reconfigures logging at runtime, this is the trap.

### "Could not unwrap this organization's data key"

A sync that reads the customers and items, then stops before the first month of
documents with `KeyUnavailable`, has a tenant data key that no longer opens
under the current `CREDENTIAL_ENCRYPTION_KEY`. The pull no longer dies on it —
it records an unresolved item and reads the documents — but the encrypted copy
of names is not being written until this is settled.

Two states look identical and have **opposite** remedies. Find out which one
this is before doing anything:

```bash
cd backend && python3 -m app.trust.rekey
```

It reports whether the stored connection credentials still decrypt. They are
encrypted under the same master key, so:

| What it says | What happened | What to do |
|---|---|---|
| Credentials **decrypt** | The master key in force is the right one; the key row is older than a rotation the rest of the database already went through — typically an organization connected under the dev default, then given a real key, then reconnected. | The old value is what would read it. If it still exists, restore it and re-run a sync. If it does not, `python3 -m app.trust.rekey --reissue --reason "…"` issues a fresh key. |
| Credentials do **not** decrypt | `CREDENTIAL_ENCRYPTION_KEY` itself changed. | Restore the previous value — nothing is lost. Do not reissue; the tool refuses anyway. |

Reissuing is irreversible and costs exactly what was written under the old key.
The name vault is rebuilt by the next sync (the names are also held plaintext
as a display cache), so the real cost is the model-payload log: what was sent to
an AI provider before the reissue can never be read again. The tool prints those
row counts before it acts, and the reason is recorded on the key row.

It will not touch a key that was **destroyed** — that is an erasure, and it
stays irreversible.

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
