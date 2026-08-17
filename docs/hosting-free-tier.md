# Hosting — free tier (Vercel + Railway + Neon)

The variant of [hosting.md](hosting.md) for a free-tier launch instead of a
single VM. Same images, same migration discipline — `deploy/backend.Dockerfile`
and `deploy/release.sh` are unchanged and reused as-is. What differs is the
topology: no Caddy, no shared origin, three separate platforms instead of one
Compose stack.

```
   browser ──── Vercel (frontend/, static build)
                  │
                  │  vercel.json rewrite: /api/* → Railway backend
                  ▼
                Railway (deploy/backend.Dockerfile, FastAPI)
                  │
                  ▼
                Neon (Postgres)
```

Because Vercel rewrites `/api/*` to the Railway URL server-side, the frontend
keeps using the same relative `/api/v1/...` paths it already uses in dev and in
the self-hosted deploy (`frontend/src/platform/api.ts`) — no frontend code
changes, and the browser only ever talks to one origin, so `CORS_ORIGINS` can
stay empty exactly as it does in the Caddy setup.

Redis is not provisioned in this variant. `compose.yaml` provisions it only for
future cross-replica state; nothing today requires it, so `REDIS_URL` is simply
left unset and every candidate that would use it runs in its in-process
fallback (see `docs/operations.md`).

---

## 1. Neon (database)

1. Create a project at neon.tech. Note the connection string it gives you —
   it looks like `postgresql://user:pass@ep-xxx.neon.tech/neondb?sslmode=require`.
2. Rewrite it for SQLAlchemy's driver, the same way `compose.yaml` does:
   `postgresql+psycopg://user:pass@ep-xxx.neon.tech/neondb?sslmode=require`
   This full string is your `DATABASE_URL`.
3. Nothing else to do here yet — migrations run from Railway in step 3.

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

3. Deploy. Railway will give the service a public URL
   (`https://<something>.up.railway.app`) — copy it, you need it in step 3.
4. Run the release step once — migrations and seed — the same deliberate,
   separate step `hosting.md` uses, not folded into the boot command:

   ```bash
   railway run --service <your-service-name> bash deploy/release.sh
   ```

   (Requires the Railway CLI: `npm i -g @railway/cli && railway login`, run
   from the repo root after `railway link`.) Re-running this after a later
   schema change is the same command — it is idempotent and reports
   `BEFORE`/`AFTER` migration state exactly as it does in the self-hosted flow.

5. Confirm: `curl https://<your-railway-url>/api/health` should report the
   schema as `CURRENT`, the same check `hosting.md` uses.

## 3. Vercel (frontend)

1. New project → import this repo → set **Root Directory** to `frontend`.
   Vercel will pick up `frontend/vercel.json` for the build command, output
   directory, and the `/api/*` rewrite.
2. Before the first deploy, edit `frontend/vercel.json` and replace
   `REPLACE_WITH_RAILWAY_URL` with the Railway URL from step 2.3 (just the
   host, no `https://` prefix duplicated — see the existing rewrite line).
   Commit and push; Vercel redeploys on push.
3. Deploy. Open the Vercel URL and sign in with `s.menon@…` / the
   `SEED_PASSWORD` you generated — same three seeded accounts as
   `hosting.md` describes, flagged `must_change_password`.

---

## Differences from the self-hosted deploy, summarized

| | Self-hosted (`hosting.md`) | Free tier (this doc) |
|---|---|---|
| Edge / TLS | Caddy, one origin | Vercel edge + Railway edge, joined by a rewrite |
| `CORS_ORIGINS` | empty (same origin via Caddy) | empty (same origin via Vercel rewrite) |
| Redis | provisioned, unused today | not provisioned, unused today |
| Release step | `docker compose --profile release run --rm release` | `railway run ... bash deploy/release.sh` |
| Images | built and run by Compose | `deploy/backend.Dockerfile` built by Railway; frontend built natively by Vercel, not via `deploy/web.Dockerfile` |

Everything else — env var meaning, the AI-spend go-live gate, the
`ZOHO_SOURCE` switch, what `/api/health` reports — is unchanged; see
`docs/operations.md`.

## Moving to AWS later

Nothing above is a dead end. `deploy/backend.Dockerfile` is the same image
App Runner or ECS Fargate would run; `DATABASE_URL` pointing at Neon becomes
`DATABASE_URL` pointing at RDS; `deploy/release.sh` becomes a one-off ECS task
instead of a `railway run`. The migration is a config change, not a rewrite.
