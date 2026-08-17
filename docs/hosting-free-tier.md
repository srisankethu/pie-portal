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

Redis is not provisioned in this variant. `compose.yaml` provisions it only for
future cross-replica state; nothing today requires it, so `REDIS_URL` is simply
left unset and every candidate that would use it runs in its in-process
fallback (see `docs/operations.md`).

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

   `UVICORN_WORKERS=1` rather than the Compose default of 2: free-tier Railway
   gives you far less RAM than the 4 GB `hosting.md` recommends, and each
   worker warms its own ~13 MB catalogue copy.

   **Put the Railway service in the same region as the Neon project.** This is
   not a tuning nicety. Every DDL statement and every query is a round trip, so
   a Singapore database behind a US service pays ~170 ms per trip — enough to
   turn a first-boot migration from seconds into minutes, and to make every
   screen feel slow afterwards. Railway sets the region per service; Neon shows
   its region in the connection host (`…ap-southeast-1.aws.neon.tech`).

3. Deploy. Railway will give the service a public URL
   (`https://<something>.up.railway.app`) — copy it, you need it in step 3.
4. **Run the release step once — before trusting the deploy.** Migrations and
   seed, the same deliberate step `hosting.md` uses and never folded into the
   boot command. Run it from your workstation: Neon is reachable from
   anywhere, so nothing has to happen inside the container.

   ```bash
   cd backend && pip install -r requirements.txt      # once
   cd ..
   DATABASE_URL="<the Neon URL from step 1>" \
     SEED_PASSWORD="<the same value you set on Railway>" \
     bash deploy/release.sh
   ```

   Re-running it after a later schema change is the same command — it is
   idempotent and reports `BEFORE`/`AFTER` migration state exactly as it does
   in the self-hosted flow.

   Note that `railway run` is *not* the way to do this: it executes the
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

   **Until step 4 has run, this endpoint returns 503 and the Railway
   healthcheck fails the deployment.** That is correct behaviour, not a
   misconfiguration: an empty database is `EMPTY`, not healthy (CLAUDE.md §4),
   and `AUTO_BOOTSTRAP` is ignored in production so the app will never migrate
   itself. Pointing `DATABASE_URL` at a fresh database *always* means migrating
   it before the next deploy can go green.

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
| Redis | provisioned, unused today | not provisioned, unused today |
| Release step | `docker compose --profile release run --rm release` | `DATABASE_URL=... bash deploy/release.sh` from a workstation |
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
