# Getting started

A complete, from-nothing setup guide. Follow it top to bottom and you will have
the application running locally with realistic demo data.

**Time:** ~15 minutes, most of it downloads.

---

## 1. What you are about to run

Two things live in this repository and share one backend:

| Surface | What it does |
|---|---|
| **Commercial Decision Platform** | Reads the company's Zoho sales/cost history, detects the handful of commercial situations worth a human decision, has an AI interpret those facts, and routes the result to the right person. |
| **Quote Builder** | Turns a pasted RFQ into priced quote lines using the PIE product-intelligence engine, with the platform's decision support alongside each line. |

The one rule that explains most of the design: **the AI never computes a
number.** Every figure comes from deterministic backend arithmetic over source
records; the AI only interprets those figures. See
[architecture.md](architecture.md).

---

## 2. Prerequisites

| Tool | Version | Check |
|---|---|---|
| Python | 3.11 or newer | `python --version` |
| Node.js | 20 or newer | `node --version` |
| Git | any recent | `git --version` |

You do **not** need a database server. Development uses SQLite, which is part of
Python's standard library.

You do **not** need an AI API key. The default provider is a deterministic
offline mock, and the whole app works without network access to a model.

> **Windows:** everything below works in PowerShell. `make` is not available on
> Windows, so use the raw commands shown in each step rather than the `make`
> shortcuts. Where a path is shown with `/`, use `\` in PowerShell.

---

## 3. Get the code

```bash
git clone --recurse-submodules https://github.com/srisankethu/pie-portal
cd pie-portal
```

Already cloned without `--recurse-submodules`? Section 4 fixes that.

---

## 4. Fetch the PIE engine

The Quote Builder resolves product codes using **pie-parser**, a separate
private repository, vendored here as a **git submodule** at `./pie-parser`. The
commit it is pinned to lives in this repository's index, so the integration
always builds against a known-good engine revision and moving the pin is a
reviewable one-line commit.

The same command works everywhere, including Windows PowerShell:

```bash
git submodule update --init --recursive pie-parser
```

On macOS / Linux `./scripts/setup_pie_parser.sh` runs exactly that and, if it
fails, prints the credential options below rather than git's terser message.

Do **not** clone pie-parser separately and check out a commit by hand. The pin
is recorded in one place — the index — and a SHA copied into a shell history or
a document is the copy that goes stale silently.

pie-parser is **private**, so this step needs GitHub credentials. If the fetch
fails with an auth error, configure a credential helper / personal access token
(`gh auth login`), or rewrite the remote to SSH once, globally:

```bash
git config --global url."git@github.com:".insteadOf "https://github.com/"
```

Already have a checkout elsewhere? Skip this step and point at it instead:

```bash
export PIE_PARSER_ROOT=/path/to/pie-parser     # PowerShell: $env:PIE_PARSER_ROOT="..."
```

> **Can't get access yet?** You can still run everything else. The Decision
> Platform does not use pie-parser at all. Only the Quote Builder's product
> resolution needs it, and without it each quote line simply shows
> `PIE OFFLINE` instead of failing.

---

## 5. Install dependencies

```bash
# Backend
python -m pip install -r backend/requirements.txt

# Frontend
cd frontend
npm install
cd ..
```

A virtual environment is recommended but not required:

```bash
python -m venv .venv
source .venv/bin/activate          # PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements.txt
```

---

## 6. Build the product catalogue

Each connected company decodes its own item-master export, into its own
catalogue at `backend/data/catalogues/<connection_id>/products.jsonl`. They are
deterministic and large (~13 MB each), so they are gitignored and built locally.

pie-parser ships a corpus of 6,717 real Kennametal/WIDIA nomenclature rows, and
that corpus is the **seed**: your first connected company inherits it, once, so
a fresh install resolves without uploading anything. After that a company
uploads its own export and the seed is never read for it again.

```bash
python scripts/build_catalog.py
```

That seeds each organization's first company and builds what is missing. You
can skip it — the app does both at start-up — but doing it now makes the first
quote much faster.

There is no need to remember this command later: **Setup → Decoded catalogue**
reports, per company, whether a catalogue exists, which pack, version and
ruleset checksum built it, and rebuilds it on a button. That screen is the one
to use once the app is running; this script is here because step 6 happens
before there is an app to click in.

---

## 7. Configure (optional)

Every setting has a working default. To customise:

```bash
cp .env.example .env
```

`.env` at the repository root is loaded automatically at startup. A real
environment variable always wins over the file, so an export or a container's
environment overrides it. `.env` is gitignored — never commit secrets.

For local development you can skip this entirely.

---

## 8. Run it

**Two terminals.**

Terminal 1 — backend:

```bash
cd backend
python -m uvicorn app.main:app --reload --port 8000
```

Terminal 2 — frontend:

```bash
cd frontend
npm run dev
```

Then open **http://localhost:5173**.

On macOS/Linux, `make dev` runs both at once.

### What happens on first startup

The backend **bootstraps its own database** — you do not run migrations or a
seed script by hand:

1. creates `backend/data/` if missing;
2. applies all Alembic migrations to head;
3. seeds the organization and the three demo users;
4. seeds a realistic demo dataset (5 customers, 4 products, and the sales/cost
   history that triggers all five signal types).

It is idempotent, so it runs safely on every restart. To do it explicitly:

```bash
cd backend
python -m app.bootstrap
```

This is disabled in production — see [operations.md](operations.md).

---

## 9. Sign in and look around

Sign in with **any password**:

| Email | Role | What they see |
|---|---|---|
| `r.nair@pie.example` | Salesperson | Only their assigned customers. **No cost or margin anywhere** — those facts are removed server-side, not hidden in the UI. 2 open decisions. |
| `m.rao@pie.example` | Sales manager | The whole organization, including margin and cost pass-through. 5 open decisions. |
| `s.menon@pie.example` | Owner | Everything the manager sees, plus the AI cost/health metrics endpoint. 5 open decisions. |

Worth doing on your first run, in this order:

1. **Sign in as the owner.** The home screen lists the open decisions. Open one:
   the left side is *facts read from source systems*, the right is *the AI's
   reading of them*. That separation is the product.
2. **Switch to the salesperson** using the role switcher. Notice the two
   margin/cost decisions are **gone entirely**, not greyed out, and no cost or
   margin appears anywhere.
3. **Open "Data & AI states".** These are the designed states for when the AI is
   unavailable, withheld, degraded, or when data is restricted.
4. **Go to Quotes → Paste RFQ → Use sample RFQ → Resolve & add.** The Quote
   Builder is a screen in the same shell, on the same account — there is no
   second sign-in. Click a line to open its drawer: the same facts-vs-AI split
   appears as decision support while pricing.

---

## 10. Run the tests

```bash
cd backend
python -m pytest -q
```

Expect **138 passing**. Five further tests are deselected by default: they call
a real AI provider and cost money. To run those (needs `ANTHROPIC_API_KEY`):

```bash
python -m pytest -m live
```

They skip cleanly if no key is set.

The frontend type-checks as part of its build:

```bash
cd frontend
npm run build
```

---

## Troubleshooting

**`sqlite3.OperationalError: no such table: users`** on sign-in, or
**`unable to open database file`**

The database was never created. Startup normally handles this, so you will only
see it if `AUTO_BOOTSTRAP=0`, if `APP_ENV=production`, or if the bootstrap
logged an error. Fix it directly:

```bash
cd backend
python -m app.bootstrap
```

**Want a completely clean slate?** Delete the database and restart — it is
rebuilt and re-seeded automatically:

```bash
rm backend/data/platform.db          # PowerShell: del backend\data\platform.db
```

**`alembic upgrade head` fails with `table ... already exists`**

The database has tables that Alembic did not create, so its recorded version is
behind reality. This happens if the schema was ever created directly from the
ORM metadata — including by the bootstrap's fallback path, which logs a warning
when it is used. Either stamp the current state:

```bash
cd backend
python -m alembic stamp head
```

…or, in development, just start over:

```bash
rm backend/data/platform.db && python -m app.bootstrap
```

**Every quote line shows `PIE OFFLINE`**

pie-parser is missing, or this quote's company has no catalogue. **Setup →
Decoded catalogue** says which of the two it is, per company — an uninitialised
submodule, a missing corpus file and a company that has uploaded nothing are
three different fixes, and the screen names the one you have. Note that a quote
resolves against the catalogue of the company it was raised from and never
another's, so one company can be fine while another is empty. Re-run step 4 if
the engine is absent, then rebuild from that screen (or
`python scripts/build_catalog.py`). Confirm
`PIE_PARSER_ROOT` points at a real checkout. The Decision Platform is
unaffected by this.

**Backend starts slowly**

The PIE engine warms its catalogue at startup. Skip it during backend-only work:

```bash
PIE_WARM=0 python -m uvicorn app.main:app --reload --port 8000
```

**Frontend loads but every API call fails**

The backend is not running on port 8000, or is on a different port. Vite proxies
`/api` to `http://localhost:8000` (see `frontend/vite.config.ts`); override with
the `API_TARGET` environment variable.

**`ModuleNotFoundError: No module named 'app'`**

Run backend commands from inside the `backend/` directory.

**Port already in use**

Pass a different port: `--port 8001` for the backend, or `npm run dev -- --port 5174`
for the frontend (then set `API_TARGET` accordingly).

---

## Where to go next

- [architecture.md](architecture.md) — how the system is built and why
- [development.md](development.md) — codebase map, tests, migrations, conventions
- [operations.md](operations.md) — configuration reference and production deploy
