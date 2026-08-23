# Hosting

Running pie-portal on a machine you control, with Docker Compose.

This is the *packaging* layer. What every setting means, what the operational
endpoints do, and how to read the AI health band all live in
[operations.md](operations.md) — this document does not repeat them, because two
copies of a configuration reference is how one of them ends up wrong.

---

## What it deploys

```
                    :80 ── ACME challenge, then redirect to :443
   internet ────────:443 ─┐
                          │
                     ┌────▼──────────────────────────────┐
                     │  web   Caddy                      │
                     │        · certificate, renewed     │
                     │        · /srv  ← the built app    │
                     │        · /api/* → api:8000        │
                     └────┬──────────────────────────────┘
                          │  one origin, so the app addresses
                          │  the API as "/api/v1/..." and CORS
                          │  never enters the picture
                     ┌────▼──────────────────────────────┐
                     │  api   uvicorn · FastAPI          │
                     └────┬──────────────┬───────────────┘
                          │              │
                     ┌────▼─────────┐ ┌──▼────────────────┐
                     │  db          │ │  redis            │
                     │  Postgres 17 │ │  provisioned; no  │
                     │  (volume)    │ │  feature requires │
                     └──────────────┘ │  it yet (volume)  │
                                      └───────────────────┘

   release   one-shot: alembic upgrade head, then seed. Behind a profile,
             so `up` can never migrate anything.
```

Only `web` publishes ports. Postgres and Redis are reachable on the compose
network and nowhere else.

### The files

| File | What it is |
|---|---|
| `compose.yaml` | The stack. Also the place the backend's environment is defined, once, shared by `api` and `release`. |
| `deploy/backend.Dockerfile` | API image: dependencies, the app, pie-parser, and the catalogue decoded at build time. |
| `deploy/web.Dockerfile` | `npm ci && npm run build`, then the output copied into Caddy. |
| `deploy/Caddyfile` | TLS, the `/api` proxy, SPA fallback, cache and security headers. |
| `deploy/release.sh` | Migrations and seeding — the deliberate step. |
| `deploy/production.env.example` | The template for `.env.production`. |

---

## Before you start

- A Linux machine with Docker Engine and the Compose plugin. Two CPUs and 2 GB
  of RAM is enough for the fixture dataset; give it 4 GB before a live Zoho
  account, because a pull holds a lot of rows in flight.
- **A hostname whose A/AAAA record already points at that machine.** Caddy
  proves control of the name over port 80 before it is issued a certificate, so
  DNS has to be correct *first* — this is the step that most often turns a
  ten-minute deploy into an hour.
- **Ports 80 and 443 open from the internet.** 80 is not just a redirect; it is
  how the certificate is issued and renewed.
- **pie-parser, if you want the Quote Builder.** It is a private submodule, so
  the build machine needs credentials for it:

  ```bash
  git submodule update --init --recursive pie-parser   # or ./scripts/setup_pie_parser.sh
  ```

  Without it everything else still runs. `deploy/backend.Dockerfile` treats a
  missing pie-parser as non-fatal, the API logs `pie-parser not found at
  /app/pie-parser` at startup, and every quote line reports **PIE OFFLINE** —
  the degradation the Quote Builder is designed around. A deploy that cannot
  reach a private repository should lose one surface, not fail to boot.

---

## First deploy

```bash
git clone --recurse-submodules https://github.com/srisankethu/pie-portal
cd pie-portal

cp deploy/production.env.example .env.production
$EDITOR .env.production          # every blank value has a generator command beside it

make deploy-build                # ~3 minutes; builds both images
make deploy-release              # migrations + seed
make deploy-up
```

Then open `https://<your SITE_ADDRESS>` and sign in with one of the three seeded
accounts (`s.menon@` owner, `m.rao@` manager, `r.nair@` salesperson) using
`SEED_PASSWORD`. Every one is flagged `must_change_password`, so that value is
what each person signs in with **once**.

### Why release is a separate command

`make deploy-up` does not migrate, and cannot. Nothing in the stack depends on
the `release` service — it sits behind a compose profile and runs only when
somebody names it. That is [operations.md](operations.md)'s "schema changes
become a deliberate, reviewed deploy step" expressed in the topology rather than
in a comment, and it is there because of the incident recorded in CLAUDE.md §4.

The consequence worth knowing: **between `up` and `release`, the API is
deliberately unhealthy.** `/api/health` returns 503 and names every missing
table. That is the system working — a health check that passes on a database
the code cannot serve is a health check nobody should route on.

```
$ curl -s https://pie.example.com/api/health | python3 -m json.tool
{
    "ok": false,
    "migration": {
        "state": "EMPTY",
        "summary": "This database is empty. Run `alembic upgrade head` to create the schema."
    },
    ...
}
```

`release` prints the migration state before and after, so the log records what
the deploy actually did rather than only that it succeeded:

```
[release] before: This database is empty. Run `alembic upgrade head` to create the schema.
INFO  [alembic.runtime.migration] Running upgrade  -> 41730a334a54, phase1 decision platform foundation
...
[release] after: Database is at head (bcf507053964).
Seeded organization org_pie with 3 demo users.
```

It is idempotent. Re-running it on a current database prints `before:`/`after:`
at the same revision and leaves every existing password alone.

---

## Upgrading

```bash
git pull
make deploy-runbook              # what this range's migrations actually do
make deploy-build
make deploy-release              # only if that runbook lists migrations
make deploy-up                   # recreates changed containers
```

`make deploy-runbook` reads the migrations between two revisions and reports
which of them rewrite tables or destroy data, so the backup decision is made
from evidence. It deliberately does **not** guess what the deployment is
currently at — ask `/api/health`, which knows.

Order matters: build, then release, then up. Migrating before the new image
exists means a rollback has nowhere to roll back to.

---

## Backups

Postgres holds two kinds of data and only one of them is recoverable
([operations.md](operations.md#backup-and-recovery) has the reasoning):

- the **read model** — customers, products, sales, costs — is a projection of
  Zoho and can be rebuilt by re-running the sync;
- **platform state** — signals, decisions, the human action taken on each one,
  AI telemetry — **exists nowhere else**. Re-syncing does not bring it back.

```bash
# Dump (covers both). `pipefail` is load-bearing, see below.
set -o pipefail
docker compose --env-file .env.production exec -T db \
  pg_dump -U pie_portal pie_portal | gzip > pie-portal-$(date +%F).sql.gz

# Restore into an empty database, then re-run the sync to bring the read model current.
set -o pipefail
gunzip -c pie-portal-2026-08-07.sql.gz | \
  docker compose --env-file .env.production exec -T db \
    psql -v ON_ERROR_STOP=1 -U pie_portal -d pie_portal
```

**Both of those flags are the difference between a backup and a file.** A shell
pipeline reports its *last* command's status, so without `pipefail` a `pg_dump`
that dies halfway still exits 0 and leaves a perfectly valid gzip archive of
nothing. And `psql` logs a failed statement and carries on by default, so
without `ON_ERROR_STOP=1` a restore that dropped half the tables also exits 0.
Neither flag was here until `scripts/restore_drill.py` was written and had to
decide what "the restore succeeded" meant.

Put that first command on a schedule and copy the output off the machine. A
backup that lives only on the host it backs up is not one.

### The procedure is tested

`scripts/restore_drill.py` runs exactly the two commands above — the same
`pg_dump | gzip` and `gunzip -c | psql` pipelines, through `bash`, against a
disposable server — on every `make verify`. It seeds a database with
representative data including the platform state a re-sync cannot rebuild, dumps
it, restores into an empty database, and compares: every table's row count,
every row column-for-column, every `Decimal` money column's exact Σ, every audit
chain re-verified through `trust/audit.verify` with its head hash, and every
erasure receipt re-verified through `trust/erasure.verify_receipt`.

The audit chain is the reason it runs every time rather than once. Its entries
are HMAC-linked and anchored in `audit_chain_heads`, so **a restore that brings
the chain back in a state which fails `verify` is indistinguishable, from the
operator's chair, from somebody having tampered with the log** — and the failure
mode is real: `timestamptz` renders in the session's TimeZone, so a server set
to `Asia/Kolkata` would hash every entry differently if `audit.covered_body` did
not normalise to UTC first.

What the drill does **not** prove is the shorter and more important list. It
dumps a database it created seconds earlier, so it says nothing about whether
any *particular* backup file on disk is good, nothing about that file being
stored anywhere durable or off this host, and nothing about how long a restore
takes at production volume — the seed is a few hundred rows so the drill costs
the gate seconds. Verifying an actual backup is still a thing a person has to
do; this only removes the excuse that the procedure itself was never tried.

Worth keeping too: the `caddy-data` volume, which holds the issued certificate
and the ACME account key. Losing it is survivable — Caddy re-issues — but it
spends Let's Encrypt rate limit to do so.

---

## Operating it

The sync → detect → decide cycle, the operational endpoints, and the AI health
band are all in [operations.md](operations.md#runbook). Two container-specific
notes:

```bash
make deploy-logs                                        # follow everything
docker compose --env-file .env.production logs -f api   # just the backend
docker compose --env-file .env.production exec api sh   # a shell in the API
```

### The scheduled sync

```bash
docker compose --env-file .env.production exec -T api python -m app.sync_all
```

That pulls **every enabled connection at once** and then analyses the
organization **once**, after all of them. Both halves matter:

- Zoho meters its API per company and every imported table is keyed on
  `connection_id`, so the pulls are genuinely independent. Three companies that
  took three hours end to end now take about as long as the slowest one.
- Detectors, metrics, business state and decisions are scoped to the
  *organization*, whose read model all of its connections feed. Running them
  per-connection did not just cost three times over — the first pass analysed a
  book that was two thirds unread and spent real AI calls on it.

It blocks until the cycle is done, so two nightly runs cannot overlap, and it
exits non-zero if any pull failed or came back `PARTIAL` — which is what makes
cron mail you the night Zoho rate-limited the run.

### Two cadences, and why it is two

The nightly pull is **incremental**: it asks Zoho for the most recently modified
documents first and stops as soon as it reaches something it already holds. The
resume cursor already avoided re-*fetching* an unchanged document; this avoids
re-*listing* it too, which is the rest of the bill — two years of history is one
list call per 200 documents, every night, almost all of it spent discovering
that nothing moved.

What that buys costs one thing, and it is worth understanding rather than
discovering: a listing that stopped early has not seen the whole book, so it
cannot tell you about a document that was **deleted or voided** in Zoho. The
code refuses to guess — an early-stopped listing is not marked complete, and the
deletion sweep declines to run against it. Nothing is silently pruned; the
mirror simply lags on deletions until a full listing happens.

That is what `--reconcile` is for. It lists everything while still skipping
unchanged documents, so it costs list calls only — minutes, not the original
hour — and it is the only mode that notices a deletion.

```bash
# Nightly at 01:30 — incremental. Minutes.
(crontab -l 2>/dev/null; echo '30 1 * * * cd ~/pie-portal && docker compose --env-file .env.production exec -T api python -m app.sync_all >> ~/sync.log 2>&1') | crontab -

# Sundays at 02:30 — full listing, so deletions and voids land. Still minutes.
(crontab -l 2>/dev/null; echo '30 2 * * 0 cd ~/pie-portal && docker compose --env-file .env.production exec -T api python -m app.sync_all --reconcile >> ~/sync.log 2>&1') | crontab -
```

| Mode | Detail calls | List calls | Sees deletions | Use |
|---|---|---|---|---|
| default | changed only | stops at the mark | no | nightly |
| `--reconcile` | changed only | whole window | **yes** | weekly |
| `--full` | **every document** | whole window | yes | a read model you no longer trust |

`--full` is the original hour-long pull. It is not a schedule; it is a repair.

Other flags: `--since YYYY-MM-DD` sets the start date, `--organization` limits
it to one tenant (repeatable), `--json` emits the raw result.

Host cron rather than a scheduler container: one less thing to keep alive, and
it fails in the place you already read mail from. The Data screen's own Sync
button is unaffected — it still starts one company and returns immediately.

---

## Troubleshooting

**The certificate never issues.** Almost always DNS or a closed port 80. Check
the name resolves to this machine from *outside* it, then read
`make deploy-logs`; Caddy states the ACME failure plainly. Use
`SITE_ADDRESS=:80` to confirm the rest of the stack works while you sort DNS
out — but do not leave a deployment there.

**`/api/health` is 503.** Read it — it names the state and the gap. `EMPTY` or
`BEHIND` means `make deploy-release`. Any other state: the table in CLAUDE.md §4
says what each one actually needs, and for two of them upgrading is the wrong
move.

**`compose.yaml: required variable AUTH_SECRET is missing`.** The `:?` guards in
`compose.yaml` fired. Compose names the variable; the generator command for it
is beside it in `deploy/production.env.example`.

**`release` exits with "SEED_PASSWORD is unset, or still the default".**
Working as intended — see the gates below.

**Quote lines all show PIE OFFLINE.** pie-parser was not in the build context.
Check out the submodule and rebuild; nothing else is affected.

**A build fails fetching packages inside the container.** If the machine is
behind a TLS-inspecting proxy, the container has to trust its CA. Add it to the
base image rather than disabling verification.

---

## What is still open before real customer data

Two of the three gates [operations.md](operations.md#before-going-live--known-gates)
lists are open, and one has since closed. Stating them accurately matters more
than stating them reassuringly:

1. **Authentication is real, but it is not an identity provider.** ~~Any
   password for a known email~~ — that is no longer true: `routers/platform_auth.py`
   verifies a PBKDF2 hash, a user row without one cannot sign in at all, and
   wrong credentials get a single indistinguishable 401. What you do *not* have
   is SSO, MFA, per-person provisioning, or a password-reset flow that does not
   go through an operator running `python -m app.seed --set-password`. For a
   handful of internal users behind a strong `SEED_PASSWORD` that is a defensible
   place to start. It is not where a public deployment should stay.

2. **Customer → salesperson assignment.** Still open. The sync maps the
   salesperson on a customer's most recent invoice to a platform user on an
   exact email match, and a salesperson only sees accounts where
   `assigned_user_id == user_id` — so any account it cannot map is invisible to
   the person who should act on it. After the first live sync, check **Skipped
   rows** on **Data & connection** for `UNMAPPED_SALESPERSON` and
   `ASSIGNMENT_UNAVAILABLE`.

3. **The AI cost rates.** Still open until you edit them.
   `AI_COST_PER_MTOK_INPUT` and `AI_COST_PER_MTOK_OUTPUT` default to indicative
   values, and every figure on the owner's AI-spend screen derives from them.
   Leaving them produces a confident wrong number, which is worse than a missing
   one.

Until gate 2 is closed, run with `ZOHO_SOURCE=fixture`. It is deterministic
offline data, and it is also what stops a fabricated customer from ever
appearing beside a real one: the demo seed is ignored outright in production and
whenever the source is live.
