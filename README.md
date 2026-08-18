# pie-portal — Commercial Decision Platform + Quote Builder

An AI-native commercial decision-support system for a B2B industrial
cutting-tool distributor running on Zoho Books — with connectors for the
ERPs US distributors actually run: Oracle NetSuite, Dynamics 365 Business
Central, Acumatica, Epicor Prophet 21, and Sage X3 / Sage 100
(see `docs/connectors.md`).

It reads the company's own sales and cost history, detects the handful of
commercial situations that genuinely deserve a human decision, has an AI
interpret those facts, and routes the result to the person who owns the call.

> **The rule that explains the design:** the AI never computes a number. Every
> figure comes from deterministic backend arithmetic over source records. The AI
> reads those figures and writes a short recommendation. A human accepts,
> modifies, or rejects it — and that decision is captured.

**Out of the box the AI is off.** `AI_PROVIDER` defaults to `mock`, a
deterministic offline stand-in: it restates the signal's own figures and says on
every card that it is doing so. The deterministic engine underneath is the
valuable half and it is real either way. To run the narrative layer on a real
model — one setting, one key, and one screen that tells you what it will cost
before you point it at a live book — see
**[Turning the AI on](docs/operations.md#turning-the-ai-on)**.

---

## New here?

**→ [docs/getting-started.md](docs/getting-started.md)** — complete setup from
nothing, macOS/Linux and Windows, in about 15 minutes.

| Guide | What it covers |
|---|---|
| [Getting started](docs/getting-started.md) | Prerequisites, install, first run, troubleshooting |
| [Architecture](docs/architecture.md) | How the system works and why it is built this way |
| [Development](docs/development.md) | Codebase map, tests, migrations, conventions |
| [Hosting](docs/hosting.md) | Running it on your own machine: Docker Compose, TLS, backups |
| [Operations](docs/operations.md) | Configuration reference, production deploy, runbook |
| [Zoho setup](docs/zoho-setup.md) | Connecting a live Zoho Books account (read-only) |
| [Role reviews](docs/reviews/) | What each role actually experienced, end to end, and the disposition of every finding |
| [Application engineering](docs/concepts/01-application-engineering.md) | How much of the item master reaches the PIE catalogue, measured — and what that does and does not justify building |

---

## Quickstart

Requires Python 3.11+ and Node 20+.

```bash
git clone --recurse-submodules https://github.com/srisankethu/pie-portal
cd pie-portal

./scripts/setup_pie_parser.sh                        # PIE engine (private submodule)
python -m pip install -r backend/requirements.txt
python scripts/build_catalog.py
(cd frontend && npm install)

# terminal 1
cd backend && python -m uvicorn app.main:app --reload --port 8000
# terminal 2
cd frontend && npm run dev
```

Open **http://localhost:5173**. The backend creates and seeds its own database
on first start — there is no separate migrate/seed step.

Sign in with the seed password — `change-me-now` unless `SEED_PASSWORD` was set.
A wrong password is rejected, so there is no "any password" shortcut.

**You will be asked to choose a password immediately, and the app is not usable
until you do.** That sentence used to say the accounts were "flagged" to change
it, which was true and did nothing: the flag was read by the sign-in response and
by a label on the admin grid, and enforced nowhere, so the password printed above
stayed live on every account indefinitely. The server now refuses a flagged
account every request except the change itself. To seed accounts that are *not*
flagged — a throwaway demo, or a test harness — set
`ISSUED_ACCOUNTS_MUST_CHANGE_PASSWORD=0`. It switches who gets flagged, never the
rule.

| Email | Role | Sees |
|---|---|---|
| `r.nair@pie.example` | Salesperson | Own customers; **no cost or margin** |
| `m.rao@pie.example` | Sales manager | Whole organization + economics |
| `s.menon@pie.example` | Owner | The above + AI cost/health metrics |

On Windows, or if anything goes wrong, see
[getting-started.md](docs/getting-started.md).

---

## The two surfaces

One backend, one frontend build, one sign-in — and two surfaces reached through
the same navigation. The Quote Builder used to be a second application in the
bundle with a login of its own; it is a screen at `#/quotes` now, on the account
you signed in with.

**Commercial Decision Platform** (primary) — detects five commercial situations,
grounds an AI interpretation on deterministic facts, and routes role-scoped
decisions:

| Category | The fact it states | Routed to |
|---|---|---|
| Customer Decline | Recent revenue materially down vs a comparable prior period | Salesperson |
| Customer Dormancy | A regular buyer has gone silent beyond its typical interval | Salesperson |
| Margin Deterioration | A product's margin has fallen, computed only from reliable cost | Manager / Owner |
| Cost Pass-Through | Purchase cost rose but selling price did not follow | Manager / Owner |
| Quote Context | On demand while quoting: history, last price, trend, cadence | Salesperson + |

**Quote Builder** — paste a messy RFQ (manufacturer codes, loose descriptions,
quantities); each line is resolved through pie-parser into a concrete supply
product with a relationship and ranked alternatives, priced against Zoho, with
the platform's decision support alongside each line.

```
   RFQ text ──▶ pie-parser (identity-first resolution + equivalence)
                     │  reqCode → supplyCode + relationship + alternatives
                     ▼
   Zoho Books ──▶ availability · list price · landed cost · estimate
                     │
                     ▼
   Pricing engine (management-only) ──▶ recommended price · margin · floor
                     │
                     ▼
   Role-gated API ──▶ React quote grid (sales view has NO economics)
```

pie-parser is **nomenclature only** — it never carries price or stock. All
commercial data comes from the Zoho layer.

| pie-parser outcome | Portal relationship |
|---|---|
| Authoritative identity (`AUTO_MATCH`/`CONFIRMED`, `SAME_PRODUCT`) | **EXACT** |
| Requirement + top equivalence score ≥ 0.85 | **TECH** |
| … 0.60–0.85 | **COMPAT** |
| … < 0.60 | **POSSIBLE** |
| `AMBIGUOUS` / `CONFLICT` | **AMBIGUOUS** (abstain, show options) |
| Unresolved / no match | **UNRESOLVED** |
| Engine failure | **PIE OFFLINE** (the line degrades; the quote never fails) |

---

## What makes it trustworthy

- **Facts and AI are structurally separate** — in the data model, in the API,
  and on screen. You always know which is which.
- **Grounding gate.** Every number in AI output must trace to a supplied fact,
  or the response is rejected and the deterministic reading is shown instead.
- **Permissions enforced server-side.** A salesperson's cost/margin facts are
  *absent* from the payload, not hidden by the UI.
- **Honest degradation.** When the AI is unavailable, times out, or fails
  validation, the facts still stand and the decision stays actionable.
- **No auto-correction of bad data.** Anomalies suppress the dependent signal
  rather than producing a confident wrong answer.
- **Full audit trail.** Immutable signals with their evidence, the exact facts
  the AI cited, and the human action taken.

---

## Layout

```
backend/app/
  signals/       the five deterministic detectors (pure functions)
  context/       permission-scoped fact bundle for the AI
  ai/            provider · prompt · grounding gate · telemetry · metrics
  decisions/     signal → decision; on-demand quote support
  ingestion/     Zoho source → normalize → idempotent sync
  domain/        ORM models · enums · schemas
  routers/       HTTP surface
  pie_service.py in-process pie-parser bridge + relationship mapping
  pricing.py     management-only recommended price + margin floor
frontend/src/
  platform/      Decision Platform UI
  components/    Quote Builder UI + decision-support panel
docs/            getting-started · architecture · development · operations
  reviews/       per-role end-to-end reviews, with each finding's disposition
```

---

## Testing

```bash
cd backend && python -m pytest -q        # 138 tests
python -m pytest -m live                 # opt-in: real AI provider (needs a key)
```

Coverage spans the deterministic detectors, the AI grounding gate and every
failure path (timeout, malformed, hallucinated number, prompt injection,
withheld), context redaction, API authorization and organization isolation, the
decision lifecycle, the Quote Builder integration, and fresh-clone startup.

---

## Status

V1 is complete and demo-ready. Before it runs on real customer data, three
deployment gates remain — replacing the demo login with a real identity
provider, mapping customer→salesperson assignment from Zoho, and setting the
real AI cost rates. The Outcome Tracker (measuring the realised impact of
accepted recommendations) is the most valuable next increment.

Details in [operations.md](docs/operations.md) and
[architecture.md](docs/architecture.md).
