# Hosting — free tier (Vercel + Railway + Neon)

The variant of [hosting.md](hosting.md) for a free-tier launch instead of a
single VM. Same images, same migration discipline — `deploy/backend.Dockerfile`
and `deploy/release.sh` are the same ones the self-hosted deploy runs. What differs is the
topology: no Caddy, no shared origin, three separate platforms instead of one
Compose stack.

```
   browser ──── Vercel (frontend/, static build)
                  │
                  │  api/proxy.ts proxies /api/* → Railway
                  ▼
                Railway (deploy/backend.Dockerfile, FastAPI)
                  │
                  ▼
                Neon (Postgres)
```

Because `frontend/api/proxy.ts` proxies `/api/*` to the Railway URL
server-side, the frontend keeps using the same relative `/api/v1/...` paths it
already uses in dev and in the self-hosted deploy
(`frontend/src/platform/api.ts`) — no frontend code changes, and the browser
only ever talks to one origin, so `CORS_ORIGINS` can stay empty exactly as it
does in the Caddy setup.

The Railway URL is read from an environment variable (`BACKEND_URL`) at
request time, not baked into `vercel.json` — `vercel.json`'s `rewrites` are
static and can't interpolate env vars, so the proxy lives in a small edge
function instead. That means the backend's URL is a dashboard setting you can
change without touching the repo.

Neither variant provisions a Redis. `compose.yaml` used to, for cross-replica
state nothing ever required; `docs/caching-and-queue.md` records why the queue
went into Postgres and the caches stayed in-process instead.

What this variant *does* need to decide is where background work runs. Railway
is a single service, so the API process drains its own queue: set
`SYNC_DISPATCH=queue` and `QUEUE_WORKER=1` on it. The compose stack splits them
into two containers instead.

---

## 1. Neon (database)

1. Create a project at neon.tech. Note the connection string it gives you —
   it looks like `postgresql://user:pass@ep-xxx.neon.tech/neondb?sslmode=require`.
   Use the **direct** host, not the `-pooler` one: the app does its own
   connection pooling (`DB_POOL_SIZE`), and PgBouncer's transaction mode
   breaks session-level behaviour SQLAlchemy relies on. Revisit that only if
   you scale past one backend instance.
2. That string is your `DATABASE_URL` as-is — `config.py` rewrites a bare
   `postgresql://` (and Heroku's `postgres://`) to `postgresql+psycopg://`,
   naming the driver this image actually ships. Writing `postgresql+psycopg://`
   yourself is equally fine; an explicitly named driver is never rewritten.
3. Nothing else to do here yet — the database is migrated in step 2.4,
   which must happen before the Railway deploy can pass its healthcheck.

## 2. Railway (backend)

1. New project → Deploy from GitHub repo → this repo. Railway will read
   `railway.json` at the repo root and build `deploy/backend.Dockerfile` with
   the repo root as build context, matching how it's built in
   `deploy-build`/CI.
2. Set these variables (Railway → your service → Variables). Same names as
   `deploy/production.env.example`; skip anything Caddy-specific
   (`SITE_ADDRESS`, `ACME_EMAIL`, `HTTP_PORT`, `HTTPS_PORT`) — Railway is its
   own edge and terminates TLS for you.

   ```
   APP_ENV=production
   DATABASE_URL=<the Neon string from step 1>
   AUTH_SECRET=<python3 -c 'import secrets; print(secrets.token_urlsafe(32))'>
   CREDENTIAL_ENCRYPTION_KEY=<python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'>
   SEED_PASSWORD=<python3 -c 'import secrets; print(secrets.token_urlsafe(18))'>
   CORS_ORIGINS=
   ZOHO_SOURCE=fixture
   AI_PROVIDER=mock
   AI_MODEL=claude-haiku-4-5-20251001
   AI_COST_PER_MTOK_INPUT=1.0
   AI_COST_PER_MTOK_OUTPUT=5.0
   DEFAULT_ORG_ID=org_pie
   DEFAULT_ORG_NAME=PIE
   DEFAULT_CURRENCY=INR
   UVICORN_WORKERS=1
   PIE_WARM=1
   AUTO_BUILD_CATALOG=0
   ```

   Add one more once you have run step 4, and it is the difference between
   row-level security being on and being decorative:

   ```
   APP_DATABASE_URL=<the same Neon database, as the pie_app role>
   ```

   Every tenant-scoped table carries a fail-closed `tenant_isolation` policy, a
   policy binds the role that issued the query, and nothing binds the owner
   Neon gave you. Step 4 creates the role; this variable is what makes requests
   use it. Leave it out and the deployment works exactly as before, with
   Python-side `organization_id` filtering as the only control and
   `/api/v1/internal/observability/health` reporting `tenant_isolation` as
   UNHEALTHY.

   `UVICORN_WORKERS=1` rather than the Compose default of 2: free-tier Railway
   gives you far less RAM than the 4 GB `hosting.md` recommends, and each
   worker holds its own copy of every company catalogue it has answered from —
   ~13 MB each, up to three resident.

   `AUTO_BUILD_CATALOG=0` above is why the first quote will report NOT BUILT
   rather than pausing: with it off, nothing is decoded at start-up and an
   owner builds each company from **Setup → Decoded catalogue** when they are
   ready. On a container this small that is the right trade — a build is ~2
   seconds of CPU per company and a boot that does several is a boot the
   health check may time out.

   **Put the Railway service in the same region as the Neon project.** This is
   not a tuning nicety. Every DDL statement and every query is a round trip, so
   a Singapore database behind a US service pays ~170 ms per trip — enough to
   turn a first-boot migration from seconds into minutes, and to make every
   screen feel slow afterwards. Railway sets the region per service; Neon shows
   its region in the connection host (`…ap-southeast-1.aws.neon.tech`).

3. Deploy. Railway will give the service a public URL
   (`https://<something>.up.railway.app`) — copy it, you need it in step 3.
4. **Run the release step once — before trusting the first deploy.** Migrations
   and seed, the same deliberate step `hosting.md` uses. Run it from your
   workstation: Neon is reachable from anywhere, so nothing has to happen
   inside the container.

   ```bash
   cd backend && pip install -r requirements.txt      # once
   cd ..
   DATABASE_URL="<the Neon URL from step 1>" \
     SEED_PASSWORD="<the same value you set on Railway>" \
     APP_DB_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')" \
     bash deploy/release.sh
   ```

   `APP_DB_PASSWORD` is what creates the `pie_app` role — NOSUPERUSER,
   NOBYPASSRLS, owner of nothing — that step 2's `APP_DATABASE_URL` then points
   at. Keep the value: you need it in that URL. Neon lets its owner role create
   roles, so this works on the free tier; a managed database that does not
   allow it is the case `docs/postgres.md` covers.

   **This one really is a first-deploy act, unlike migrating.** The role
   provisioning issues `ALTER DEFAULT PRIVILEGES`, so every table a *later*
   migration creates is reachable by `pie_app` the moment it exists, with
   nothing to re-run. That is why it can live in this manual step without
   becoming the invisible-skipped-step trap described below — and why it is not
   bolted onto `preDeployCommand`, which for the reason given next cannot carry
   a second command at all.

   **Later schema changes migrate themselves.** `railway.json` sets a
   `preDeployCommand` of `alembic upgrade head`, which Railway runs in the new
   container, with the service's variables, *before* any traffic moves to it —
   and fails the deploy without cutting over if it errors. That is the same
   ordering `release.sh` has always had (migrate, then start), and the same one
   Compose gets from running `release.sh` before `up`.

   It is one command with no `cd` and no `&&`, and that is deliberate. The
   image's working directory is already `/app/backend` — which is also the only
   reason the container's own `uvicorn app.main:app` can import `app` — so
   `alembic.ini` and its relative `script_location` resolve from there without
   help. A `cd X && …` prefix would additionally require Railway to run the
   value through a shell, and if it does not, the stage dies looking for a
   binary named `cd`: a failure that produces no Alembic output at all, which
   is indistinguishable from a database that refused the migration.

   It was not always so, and the reason it is now is worth keeping. Nothing in
   the pipeline ran migrations, so every schema change needed somebody to
   remember this command — and when it was forgotten the symptom arrived one
   deploy later, as a healthy-looking build whose every request 503'd. It went
   two revisions behind that way. A deploy step that is required, manual, and
   invisible until it is skipped is not a safety property; it is a trap that
   happens to have documentation.

   What stays manual is **seeding**, which is why step 4 still exists: it
   creates an owner account, it is a first-deploy act rather than a per-deploy
   one, and `release.sh` refuses to do it in production without a real
   `SEED_PASSWORD`. Migrating is idempotent and safe to repeat; seeding is not
   the kind of thing a pipeline should do behind you.

   Note that `railway run` is *not* the way to run this by hand: it executes the
   command on your machine with Railway's variables injected, not inside the
   container, so it buys nothing here and obscures which database is being
   migrated. Use `railway ssh` if you genuinely want to run it in the
   container.

5. Confirm: `curl https://<your-railway-url>/api/health` should report the
   schema as `CURRENT`, the same check `hosting.md` uses.

   `railway.json` sets `healthcheckTimeout` to 600s, which looks absurd for
   an app that answers in milliseconds. It is sized for the slowest
   *legitimate* first boot, not the steady state: a boot that migrates an
   empty database runs ~70 revisions, each a series of DDL round trips, and
   against a cross-region endpoint at ~170 ms that alone passes 100s —
   Neon's scale-to-zero adds ~8s before the first statement even runs.
   Bootstrap happens inside the startup lifespan, so uvicorn accepts no
   request until it finishes, and a window shorter than the migration kills
   a deploy that was succeeding. Steady-state boots never approach it.

   **A 503 here means the schema is not able to serve the code**, and the body
   names the gap. That is correct behaviour rather than a misconfiguration: an
   empty or behind database is not healthy (CLAUDE.md §4), and `AUTO_BOOTSTRAP`
   is ignored in production, so the *application process* still never migrates
   itself — the `preDeployCommand` above is a separate, ordered step in the
   deploy, not the app reaching for its own schema at boot.

   If a deploy goes red here, read the health body before acting: `BEHIND`
   means the pre-deploy step failed or was skipped (Railway logs it as its own
   deployment phase), while `UNSTAMPED` or `UNKNOWN_REV` are different states
   with different fixes, and the table in CLAUDE.md §4 is the one to use.

## 3. Vercel (frontend)

1. New project → import this repo → set **Root Directory** to `frontend`.
   Vercel will pick up `frontend/vercel.json` for the build command and
   output directory, and will build `frontend/api/proxy.ts` as a serverless
   (edge) function. `/api/*` reaches it through an explicit rewrite rather
   than a catch-all filename: `api/[...path].ts` was matched for a single
   segment only, so `/api/health` worked while `/api/v1/auth/login` returned
   404 — the app loaded and every sign-in failed.

   The SPA rewrite in that file reads `/((?!api/).*)`, and **the exclusion is
   load-bearing**. A bare `/(.*)` catch-all sends `/api/*` to `index.html`
   along with everything else, and a static file answers GET but rejects POST
   with 405 — so the app loads, `/api/health` looks fine because it is a GET,
   and every sign-in fails. Real assets are unaffected either way, because
   the filesystem is checked before rewrites are applied.
2. Project → Settings → Environment Variables → add
   `BACKEND_URL` = the Railway host from step 2.3, e.g.
   `pie-portal-production.up.railway.app` (host only, no `https://`).
3. Deploy. Open the Vercel URL and sign in with `s.menon@…` / the
   `SEED_PASSWORD` you generated — same three seeded accounts as
   `hosting.md` describes, flagged `must_change_password`.

If the backend later moves (a new Railway service, a different host
entirely), update `BACKEND_URL` in Vercel's dashboard and redeploy — no
repo change needed.

---

## Differences from the self-hosted deploy, summarized

| | Self-hosted (`hosting.md`) | Free tier (this doc) |
|---|---|---|
| Edge / TLS | Caddy, one origin | Vercel edge + Railway edge, joined by `frontend/api/proxy.ts` |
| `CORS_ORIGINS` | empty (same origin via Caddy) | empty (same origin via the Vercel proxy function) |
| Backend URL config | `SITE_ADDRESS` in `.env.production` | `BACKEND_URL` env var on the Vercel project |
| Background work | `worker` container drains the queue | API process drains its own (`QUEUE_WORKER=1`) |
| Release step | `docker compose --profile release run --rm release` | `DATABASE_URL=... bash deploy/release.sh` from a workstation |
| Tenant isolation | `APP_DB_PASSWORD` in `.env.production`; compose builds `APP_DATABASE_URL` | `APP_DB_PASSWORD` on the release run, then set `APP_DATABASE_URL` on Railway by hand |
| Images | built and run by Compose | `deploy/backend.Dockerfile` built by Railway; frontend built natively by Vercel, not via `deploy/web.Dockerfile` |

Everything else — env var meaning, the AI-spend go-live gate, the
`ZOHO_SOURCE` switch, what `/api/health` reports — is unchanged; see
`docs/operations.md`.

**Why the reasoning above lives here rather than beside the settings.**
`vercel.json` and `railway.json` are validated against published schemas that
reject unknown properties, so a `_comment_…` key is not an inert annotation —
Vercel fails the deployment outright with *"should NOT have additional
property"*. JSON has no comments and these two files cannot fake them, which
is exactly why a non-obvious value in either one needs a paragraph in this
document instead.

## Moving to AWS later

Nothing above is a dead end. `deploy/backend.Dockerfile` is the same image
App Runner or ECS Fargate would run; `DATABASE_URL` pointing at Neon becomes
`DATABASE_URL` pointing at RDS; `deploy/release.sh` becomes a one-off ECS task
instead of a workstation run. The migration is a config change, not a rewrite.
