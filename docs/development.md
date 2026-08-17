# Development guide

Day-to-day work on the codebase: where things live, how to change them safely,
and the conventions that keep the guarantees true.

New here? Start with [getting-started.md](getting-started.md).

---

## Repository layout

```
pie-portal/
├── backend/
│   ├── app/
│   │   ├── main.py            FastAPI app + startup (bootstrap, PIE warm)
│   │   ├── config.py          ALL settings, read from env (+ .env)
│   │   ├── bootstrap.py       create DB → migrate → seed (idempotent)
│   │   ├── db.py              engine, session, declarative Base
│   │   ├── authz.py           platform auth: tokens, roles, scope
│   │   ├── repositories.py    org-scoped data access (the isolation seam)
│   │   │
│   │   ├── domain/            models.py (ORM) · enums.py · schemas.py
│   │   ├── ingestion/         Zoho source → normalize → idempotent sync
│   │   ├── signals/           the five deterministic detectors
│   │   ├── context/           permission-scoped fact bundle for the AI
│   │   ├── ai/                provider · prompt · contract · telemetry · metrics
│   │   ├── decisions/         signal → decision; on-demand quote support
│   │   ├── routers/           HTTP surface
│   │   │
│   │   ├── pie_service.py     PIE engine wrapper (Quote Builder)
│   │   ├── store.py           in-memory quote store (Quote Builder)
│   │   ├── pricing.py         quote pricing + margin floor
│   │   └── zoho.py            Zoho adapter for the Quote Builder
│   │
│   ├── alembic/versions/      migrations
│   └── tests/
│       ├── decision_platform/ platform tests
│       ├── live/              opt-in, real AI provider (excluded by default)
│       └── test_*.py          Quote Builder tests
│
├── frontend/src/
│   ├── platform/              app shell, routing, and the platform screens
│   ├── QuoteBuilder.tsx       the Quotes screen, inside that shell
│   ├── components/            Quote Builder UI + DecisionSupport panel
│   └── styles.css             the whole design system
│
├── scripts/                   setup_pie_parser.sh · build_catalog.py
└── docs/
```

### Following one decision end to end

The fastest way to understand the system is to trace a single decision:

1. `ingestion/sync.py` — Zoho rows → `normalize.py` → read model
2. `signals/decline.py` — a pure detector emits a `SignalDraft`
3. `signals/engine.py` — persists it as an immutable `Signal`
4. `context/assembler.py` — builds a role-scoped `ContextBundle` (drops
   RESTRICTED facts for a salesperson)
5. `ai/interpret.py` — calls the provider, validates via `ai/contract.py`,
   degrades on failure
6. `decisions/service.py` — persists a `Decision`, routed and prioritised
7. `routers/decisions.py` — serves it, scope enforced again
8. `frontend/src/platform/PlatformApp.tsx` — renders facts and AI separately

---

## The invariants

These are not style preferences. Breaking one is a bug, and each is enforced by
tests.

1. **The AI never computes a number.** Every figure originates in deterministic
   backend arithmetic over source records. `ai/contract.py` rejects any number
   in model output that cannot be traced to a supplied fact.
2. **Cost and margin never reach a salesperson.** RESTRICTED facts are removed
   server-side before the AI or the client sees them — *absent, not masked*.
3. **No silent drops, no silent mutations.** Anything skipped carries a reason
   code (see `ingestion/sync.py`'s `SyncReport`).
4. **No auto-correction of bad data.** Anomalies are flagged and *suppress* the
   dependent signal (`signals/quality.py`).
5. **Determinism.** Detectors are pure functions of
   `(snapshot, thresholds, reference_date)` — no clock, no randomness, no I/O.
6. **Backward compatibility.** New capability ships behind a config flag whose
   default reproduces prior behaviour.
7. **No business logic in the frontend.** The client formats and lays out; it
   never calculates. Every monetary figure arrives computed.

---

## Testing

```bash
cd backend
python -m pytest -q                      # the default suite (138)
python -m pytest -q tests/decision_platform/test_signals_detectors.py
python -m pytest -q -k "margin"
python -m pytest -m live                 # opt-in: real AI provider, costs money
```

`pytest.ini` sets `addopts = -m "not live"`, so the live suite never runs by
accident.

**Test layout:**

| File | Covers |
|---|---|
| `test_signals_detectors.py` | the five detectors, thresholds, withholding |
| `test_signals_quote_context.py` | on-demand quote fact assembly |
| `test_context_assembler.py` | permission scoping and redaction |
| `test_ai_contract.py` | the grounding gate |
| `test_ai_interpret.py` | every AI failure path degrades safely |
| `test_ai_observability.py` | failure taxonomy, telemetry, ops metrics |
| `test_ai_decision_service.py` | signal → decision generation |
| `test_api_authz.py` | role scope, org isolation, lifecycle |
| `test_quote_support.py` | Quote Builder ↔ platform integration |
| `test_bootstrap.py` | fresh-clone startup |
| `test_migrations.py` | migrations apply and reverse cleanly |

**Detector tests need no database.** They build an in-memory `Snapshot` via
`signal_fixtures.py`, which is why they are fast and byte-deterministic.

**AI tests need no network.** `ai/mock_provider.py` has modes for every failure
the layer must survive: `timeout`, `unavailable`, `malformed`,
`malformed_then_ok`, `hallucinate`, `cite_unknown_fact`, `injection_obeyed`,
`withheld`.

---

## Database changes

The ORM models in `domain/models.py` are the source of truth; migrations follow.

```bash
cd backend
# after editing models.py
python -m alembic revision --autogenerate -m "short description"
# review the generated file — autogenerate is a draft, not an answer
python -m alembic upgrade head
python -m alembic downgrade -1        # always verify the reverse works
```

**Write a real `downgrade()`.** `test_migrations.py` applies every migration and
then reverses all of them, asserting no tables remain — a stub downgrade fails
the suite.

Reset your local database at any time:

```bash
rm backend/data/platform.db && python -m app.bootstrap
```

**Developing against PostgreSQL** — the production dialect — is one compose
file or one script away, and the suite runs on it by setting a single
variable; [postgres.md](postgres.md) has the loop, the data-move tool for an
existing SQLite database, and the reasoning behind the dialect decisions.
SQLite stays the zero-configuration default; the gate's step 6 proves the
migration chain on Postgres either way.

---

## Common tasks

### Add a configuration setting

Add it to `Settings` in `config.py` reading from `os.environ`, give it a default
that reproduces current behaviour, and document it in `.env.example` and
[operations.md](operations.md).

### Add or change a detector

Detectors live in `signals/` and are pure functions
`(snapshot, thresholds, as_of) -> list[SignalDraft]`.

- Thresholds belong in `signals/config.py`, never inline.
- If a fact you depend on is unreliable, **withhold the signal** — do not
  compute around it. Follow the cost-reliability checks in `margin.py`.
- Every draft carries its evidence (`evidence_ref`) and a `Sufficiency`.
- Register it in `_DETECTORS` in `signals/engine.py`.
- Add fixtures to `signal_fixtures.py` and test both firing *and* not firing.

### Change what the AI sees

`context/assembler.py` builds the bundle. Facts added there become visible to
the model **and** enter the allowed-number set for grounding. Anything
cost/margin-shaped must be tagged RESTRICTED so it is dropped for salespeople —
check `RESTRICTED_FACT_FIELDS` in `domain/enums.py`.

### Add an API endpoint

Put it in an existing router in `routers/`. Take `Principal` via
`Depends(current_principal)` (or `require_manager_or_owner` / `require_owner`)
and scope every query through a repository. Return **404, not 403**, for a
record outside the caller's scope, so scope is not probeable.

### Work on the frontend

```bash
cd frontend
npm run dev            # hot reload
npm run build          # type-check + production build
```

Presentation only. If you find yourself computing a price, a margin, or a
percentage in TypeScript, it belongs in the backend. `platform/format.ts` is for
*formatting already-computed values* — nothing more.

---

## Conventions

- **Comments explain why, not what.** Match the density of the surrounding file.
- **Money is `Decimal`**, never `float`, in anything deterministic.
- **Signals are write-once.** There is no update path; a re-run inserts new rows.
- **Every query goes through an org-scoped repository.** Never remove the
  `organization_id` filter.
- **Never log prompt or response content**, or database credentials.
- Type hints throughout; `from __future__ import annotations` at the top.

---

## Debugging

```bash
SQL_ECHO=1 python -m uvicorn app.main:app --port 8000     # log every query
PIE_WARM=0 python -m uvicorn app.main:app --port 8000     # fast startup
```

Interactive API docs: **http://localhost:8000/docs**

Inspect the database directly:

```bash
sqlite3 backend/data/platform.db ".tables"
sqlite3 backend/data/platform.db "SELECT decision_type, status, priority_band FROM decisions;"
```

Watch what the AI layer is doing — sign in as the owner and call
`GET /api/v1/internal/ai-metrics`, or query `ai_call_logs` directly.
