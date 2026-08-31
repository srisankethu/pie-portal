# pie-portal — working agreement

An AI-native Commercial Decision Platform for a B2B cutting-tool distributor
running on Zoho Books, across three legal entities (SLS Engineers, 4U Precision,
UPS) — and, for US clients, on NetSuite, Dynamics 365 Business Central,
Acumatica, Epicor Prophet 21 and Sage through the connector registry in
`ingestion/erp/` (see `docs/connectors.md`). Python 3.11 / FastAPI /
SQLAlchemy 2.0 / Alembic on the backend; React 18 + Vite + TypeScript on the
front.

Read `docs/architecture.md` for the design and `docs/development.md` for the
day-to-day loop. This file is the part that constrains how code gets *added*.

**Writing or changing UI? Read `docs/ui-standards.md` first.** It is a standing
standard, not a style preference: Material UI as the design system, `Paper` for
dashboard surfaces and `Card` only for a business entity, status as a `Chip`
rather than coloured text, **AG Grid through `platform/DataGrid.tsx` for any
table whose row count is set by the size of the business — never a hand-written
`<table>`**, MUI's own loading components rather than a hand-rolled shimmer,
theme tokens rather than literals, and a shared component in `platform/kit.tsx`
wherever a pattern appears twice. New UI follows it; existing screens move
toward it as they are touched.

The grid rule is stated here because leaving it only in `ui-standards.md` §3 is
how the Quote Builder's line table stayed a hand-written `<table class="grid">`
through three UI passes: this paragraph is the summary people actually read
before writing a screen, it listed six rules, and the one it left out was the
one that screen was breaking. A rule that is not in the digest is a rule that
gets followed by whoever happens to open the long document.

That table is a `DataGrid` now (`components/LineGrid.tsx`), and the sentence
above is kept in the past tense on purpose. It is the reason the rule is in the
digest, not an open defect — a reader who greps for the fault and finds it
already fixed learns to distrust the rest of this file.

`<table>` is still right for a fact panel (a label and a value, four rows) and
for the accessible table under a chart. `platform/DataGrid.tsx` states the line;
the check in §6 finds the cases worth thinking about.

---

## 1. Invariants — these are not preferences

**AI never computes a number.** Prices, margins, priorities, thresholds and
commercial recommendations are computed by `app/commercial/` and `app/signals/`,
deterministically, from persisted rows. A model may *read* those numbers and
phrase them. It may never produce one. This is what makes an output auditable
and reproducible, and it is the reason the platform is trusted at all.

Mechanically: `commercial/`, `signals/`, `ingestion/` and `state/` must not
import `ai/`, and `ai/` must not import `commercial/`. `decisions/` is the
single seam where deterministic facts meet interpretation.

This is enforced by `tests/decision_platform/test_layer_boundaries.py`, which
parses the imports rather than grepping them, so a package named in a comment
cannot fail the build. The greps below are the same check by hand:

```bash
# Must print nothing. If it prints, the invariant is broken — fix, don't explain.
rg -n '^\s*(from|import)\s+\.*\.?ai[\. ]' backend/app/commercial backend/app/signals backend/app/ingestion backend/app/state
rg -n '^\s*(from|import)\s+.*commercial' backend/app/ai
```

**Cost and margin never reach a salesperson.** Absent from the response, not
hidden in the browser: the server omits the fields, so there is nothing to read
out of a network tab. `filterCounts.MFLOOR` was the counter-example — a
below-floor *count*, computed for every role two lines below the guard that
correctly withheld `marginFloor`, and hidden in the component by `{mgmt && …}`.
Twenty bisection probes turned it into the exact floor price, and the floor is
cost × (1 + margin floor).

**And no rule whose boundary is cost.** MFLOOR was a count; the same defect
returned as a *rule code*. `quote-intelligence/assess` takes `proposed_price`
from the caller and answers which exceptions fired, so walking the price finds
the value where the answer changes — and for `NEGATIVE_MARGIN` that value is the
purchase price itself, with no policy multiplier in the comparison to obscure
it. Two hundred lines fit in one request, so it was two round trips, not twenty
probes. Withholding the *reasoning* from a rule is not enough: **the fact that a
named rule fired is a predicate, and a predicate a caller can walk is the number
it tests against.**

So a rule carries `boundary_refs` — the values that place its boundary, not
every value it reads — and `quote_service.project` withholds any rule naming
something the recipient may not see, substituting one fixed
`APPROVAL_REQUIRED`. The control survives; the boundary does not.
`test_a_salesperson_cannot_walk_the_price_to_recover_cost` sweeps the price and
asserts the response does not change at cost, which is the shape of test this
class needs — every field-level assertion in that file passed while the endpoint
gave up cost.

Two residual boundaries remain by design, and that is the accepted line: a
control that says "this needs approval" must move somewhere. What is left is
`cost/(1 - min_margin)` and `cost/(1 - margin_floor)` — two equations in three
unknowns, so cost does not come out. **One boundary per distinct action the
recipient can take is the budget; anything past it is a leak.**

The boundary of this rule, stated because it is real rather than because it is
comfortable. A line's negotiation floor and its `recommended` price are both
cost × a policy multiplier, and a salesperson needs both to do the job; anyone
willing to do the algebra recovers cost from either. That is **accepted, not
engineered around** — coarsening those numbers would blunt the one screen this
role uses to decide rather than to read. So the rule is: no cost or margin
*field*, no count or flag that answers a margin question, and no new number
whose only purpose is economics. The rule is *not* that cost is unrecoverable.
Two corollaries, and they point in opposite directions: do not "fix" a leak by
degrading the negotiation desk, and do not wave a new field through on the
grounds that cost is derivable anyway.

**Money is `Decimal`.** Margin is a ratio (`0.24`), never a percentage. Movement
is percentage points (`_pp`). Aggregated margin is `Σ gross_profit ÷ Σ revenue`,
never the mean of per-line margins.

**Thresholds carry a version.** `CommercialThresholds.version` is a content hash
stamped on every computed row, and it is what lets the margin policy be edited
without making the numbers on screen unexplainable. Never remove it, and never
let a computed row be written without one.

What it buys is narrower than "past numbers stay explainable", so read it as:

- A row says which policy judged **the value it currently holds**.
  `customer_item_metrics` is upserted, so a full recompute overwrites the value
  and its stamp together. There is no history of superseded numbers in that
  table and there is not meant to be — it is derived state that a complete
  re-sync rebuilds from Zoho.
- History lives where rows are append-only: signals, approval requests and quote
  snapshots. Anything a human signed keeps the version that was in force when
  they signed it, which is the part an audit actually needs.
- Signals from the Signal Engine stamp `SignalThresholds.version` (`th_…`), not
  `ci_…`. That hash moves when an owner edits `queue_margin_drop_pp`, so a signal
  does say which threshold judged it — but it cannot name the commercial version
  directly. `th_` and `ci_` are not the same stamp; do not read one as the other.

**Do not weaken a rule to make output appear.** If a screen is empty because the
evidence is thin, that is the correct answer. Lowering a threshold, fabricating a
decision, or widening a band to produce a demo is a defect, not a fix.

**Absence of evidence is not a pass.** The same mistake has now been found in
three unrelated places, and in all three it read as good news. The weather front
divided profit earned on costed revenue by *all* revenue and banded a 19.7% book
POOR. The send gate found no recorded snapshot and answered "nothing is wrong",
so a line losing money on every unit went out with a green chip. A priced line
with no cost on record passed as within policy, because the only check that would
have objected sat behind `if unit_cost is not None`. When the evidence for a
claim is missing the answer is UNKNOWN, or a refusal that names what is missing —
never the benign default. The tells are worth knowing by sight: `sum(… or 0)`
over rows that may hold `None`, `if not rows: return None` in something whose job
is to refuse, and a `is not None` guard wrapped around the objection rather than
around the arithmetic.

**An equivalence score is policy, never an identity.** `pie_service._rel_from_score`
turns a score into TECH / COMPAT / POSSIBLE using this organization's
`equivalence_tech_band` and `equivalence_compat_band` — commercial policy, read
per request, versioned, and two orgs may legitimately disagree about the same
pair. So a `rel` is true of *this quote under this policy*, not of the products.
Never persist one as a relationship between products, and never feed a derived
`rel` or `supplyCode` back in as the input to another resolution.

The reason is that technical equivalence does not compose. `A ≈ B` within
tolerance and `B ≈ C` within tolerance is not `A ≈ C`, and pie-parser's engine is
built so it cannot compose them — every comparison's left operand is the request.
The one path that would smuggle a second hop past that is storage: a confirmed
mapping is *asserted* identity, and the engine will derive a requirement from an
asserted record and rank equivalents off it. So a scored suggestion promoted to a
confirmed mapping becomes an exact reference it never was, and the next "same as
their 7781 but 12 mm" composes two bands into a wrong part with a defensible
explanation attached.

Two narrow conditions hold that line: `store._identity_candidate` offers a
confirmable code only for the engine's own single-candidate `NEEDS_REVIEW`
proposal — an exact catalogue hit downgraded for namespace safety, never a scored
suggestion — and `identity.service.confirm_proposed_identity` refuses anything
else. Picking a different product is a substitution on one quote and must stay
one. `tests/test_identity_confirmation_gate.py` pins both.

**The first of those two conditions was false for as long as this paragraph has
claimed it, and the paragraph is kept because the way it failed is the lesson.**
`_identity_candidate` did not read which branch produced the candidate; it
re-derived the question from `outcome == "NEEDS_REVIEW" and len(candidates) ==
1`. Those two fields cannot answer it. `PieService._map` carries the engine's
outcome through its *suggestion* branch verbatim, so a payload with no match and
one scored suggestion arrived wearing exactly that shape — reproduced, a
`POSSIBLE` at 0.93 came back as `identity_proposal.confirmable: true`, and
`confirm_proposed_identity` checks only that the selection equals the proposal.
It would have been written as asserted identity.

The distinction — `matches` or `suggestions` — exists **only inside `_map`'s
branch structure and in none of its output fields**. So `_map` now sets
`Resolution.identity_candidate`, in one branch, and `_identity_candidate` reads
it. Everything else defaults to `None`, which is why a hand-built `Resolution`
proposes nothing until it says so on purpose.

Two general rules come out of it, and they are worth more than the fix:

- **A predicate re-derived downstream from published fields is a guess about
  what the producer meant.** If the producer knows something its output shape
  does not carry, the output shape is what has to change.
- **A test that can only speak in the output's vocabulary cannot express the
  distinction the gate turns on.** These assertions hand-built `Resolution`
  objects, so they could say "NEEDS_REVIEW with one candidate" and could not say
  "and it came from a match" — and they agreed with the wrong predicate for as
  long as it stood. They ask the real `_map` now.

**Two callers now reach that gate and it is one function on purpose.** The
Quote Builder (`routers.quote._confirm_identity`) and the public resolution API
(`routers.resolve.confirm`) both go through `confirm_proposed_identity`; the
refusal used to live inline in the first of those, and the second would have
been a copy. The API half needs its own tests rather than the shared function's,
because it has no `Line` to read `identityCandidate` off and derives the
proposal from a fresh resolution — a second site for the same computation, and
therefore the one that drifts.

---

## 2. Before writing new code — the capability search

Duplication is cheaper to prevent than to detect. Before adding any new
function, class, module or endpoint, run a capability search and answer three
questions. This is not optional, and the answers belong in the response.

**1. Does this already exist?** Search the domain verb *and* the entity, plus
synonyms — the second copy of something is almost always named differently from
the first.

```bash
# Adapt the alternation to the concept, not the name you were about to type.
rg -n --type py -e '^\s*(def|class)\s+\w*(resolve|normali[sz]|validate|compute)\w*' backend/app
rg -n --type ts  -e '(function|const|export function)\s+\w*(format|parse|when|inr|pct)\w*' frontend/src
```

**2. Does something *almost* do this?** If a close match exists, the default is
to **extend or parameterize it**, not to add a sibling. Adding a sibling needs a
stated reason.

**3. If nothing exists, where does it belong?** Check §3 before creating a file.
A concern given a second home is how responsibility duplication starts.

### What "already done right" looks like here

`commercial/quote_service.resolve_customer` does not reimplement customer
matching. It delegates:

```python
def resolve_customer(session: Session, org: str, ref: str) -> Optional[models.Customer]:
    """Reuses the Quote Builder's existing tolerant matcher — one behaviour."""
    from ..decisions.quote_support import _resolve_customer
    return _resolve_customer(session, org, ref)
```

Two tolerant matchers would drift, and a customer resolvable on the quote screen
but not in the analysis is a bug nobody can reproduce. Copy this pattern.

Likewise `frontend/src/Tip.tsx` sits outside `platform/` and is re-exported by
`platform/ui.tsx`, because both apps in this repo need the same tooltip — two
implementations would explain the same term two ways.

### Four kinds of redundancy, because they need different fixes

| Kind | What it is | How it shows up |
|---|---|---|
| **Exact / near-exact** | Same block, renamed variables | Similarity scan, or you recognise it while pasting |
| **Semantic** | Two solutions to one problem, written differently | The pre-write search above — this is the one search catches |
| **Responsibility** | Three modules each partly own one concern | Same domain term owned in several files; count the owners |
| **Abstraction** | A wrapper or interface that exists only to look SOLID | An interface with one implementer and no second one planned |

The last two are usually SOLID violations wearing a redundancy costume, which is
why §4 treats them together.

---

## 3. Module boundaries

```
backend/app/
  domain/        SQLAlchemy models + enums. No business decisions.
  commercial/    Deterministic computation: metrics, thresholds, policy,
                 quote assessment. The numbers live here. Never imports ai/.
  signals/       Detectors over persisted rows. Never imports ai/.
  state/         The event log, and what is derived from it. Append-only,
                 superseded-not-mutated, and *derived* — Zoho is the system of
                 record, so a complete re-sync rebuilds it from nothing.
                 Never imports ai/.
  decisions/     The seam: deterministic signal in, AI reading out. May import ai/.
  ai/            Providers, prompts, validation, telemetry. Receives facts;
                 never computes them. Never imports commercial/.
  ingestion/     Source adapters (Zoho + the erp/ connector registry),
                 normalisation, connections, credentials. A new source's
                 knowledge lives in its own erp/ module; the sync stays
                 connector-blind.
  enquiry/       Inbound demand: every enquiry line as it arrived, and how
                 each ended. Canonical, not derived — a re-sync rebuilds
                 nothing here. Raw customer text; never imports ai/.
  identity/      Cross-connector record linking. Never merges, only links.
  trust/         Tenant keys, name vault, pseudonyms, break-glass, disclosure,
                 erasure. Infrastructure — imports neither commercial/ nor ai/.
  routers/       HTTP mapping and role scoping. Thin — no money arithmetic.
  context/       Bundle assembly for interpretation.

  db.py               The one engine, the one Base, the one session factory.
  config.py           Every setting, including DATABASE_URL. Single source.
  migration_state.py  Where a database sits in the revision history.
  schema_check.py     Whether the schema can serve the code.
  bootstrap.py        Fresh-clone setup. Alembic only — never create_all.
  clock.py            UTC now, and making a stored timestamp comparable.
```

SQLAlchemy is used throughout by design — this is a session-passing codebase, not
a ports-and-adapters one, and pretending otherwise would mean a repository
interface per table for no gain. The boundary that matters here is
**deterministic vs interpreted**, not domain vs infrastructure.

Rules with teeth:

- A router that computes a price, a margin or a threshold is in the wrong file.
  It calls `commercial/` and maps the result.
- A new Zoho concern goes in `ingestion/`, not in the router that first needed it.
- Anything a screen renders as a number was computed in `commercial/` or
  `signals/` and persisted with a `thresholds_version`.
- `state/` is **derived by contract**: Zoho is the system of record and a
  complete re-sync rebuilds it from nothing. Something a re-sync *cannot*
  rebuild is not state, whatever its shape. An enquiry that arrived as a
  WhatsApp message exists in no ERP, which is why `enquiry/` is its own
  package and not a third pattern inside `state/` — it borrows that
  package's supersede convention and none of its lifecycle.

---

## 4. Database, schema and migrations

This section exists because of an incident. A deployment failed with a 500, the
client said "the usual cause is a pending `alembic upgrade head`", and running
that command could not have helped — the database had been built outside Alembic
and every upgrade died with `table already exists`. Dropping the tables made it
worse: the next startup rebuilt them the same way. Read the rules; the
reasoning behind each one is a day somebody lost.

### How the pieces fit

**One URL.** `settings.DATABASE_URL` (`app/config.py`) is the only place a
database location is decided, for the app and for Alembic alike. `alembic.ini`
deliberately contains **no** `sqlalchemy.url` line — a URL there is a second
source of truth and the one that wins by accident on whichever machine forgot
to set the environment. `alembic/env.py` uses an explicitly supplied URL when a
caller sets one (that is a deliberate act) and `settings.DATABASE_URL`
otherwise. Dev and production differ only in the value of that variable.

**One engine, one Base.** `app/db.py` owns the single `create_engine`, the
single `DeclarativeBase`, and `SessionLocal`. A second declarative base would
give Alembic a second metadata object it never sees, and half the models would
silently never get migrations.

**How Alembic finds the models.** `env.py` does `from app.domain import models`
purely for the import side effect, then sets `target_metadata = Base.metadata`.
Without that import the metadata is empty and autogenerate cheerfully reports
that a completely empty database is perfect. Any new model module must be
imported by `app/domain/models.py` or from `env.py`, or it does not exist as far
as migrations are concerned.

**Concurrency, on SQLite.** One process writes while others read, so the engine
sets `journal_mode=WAL`, `busy_timeout=30000` and `synchronous=NORMAL` on every
connection. This is not tuning. In the default rollback-journal mode a writer
that spills its page cache takes an EXCLUSIVE lock and holds it until commit,
blocking *every* reader — so while a background sync ran, ordinary requests
including `/api/health` failed with `database is locked`. Under WAL readers see
the last committed snapshot and never block.

The other half is transaction length: a job that writes for minutes must not do
it in one transaction. `ingestion/jobs.execute_sync` commits at each phase
boundary. That also makes progress *visible* — a flush is invisible outside its
own transaction, so the window counter the sync screen polls could not move
until the pull was already over. **On a long-running write, prefer `commit` at a
natural boundary over `flush`; a flush that nobody else can read is not progress
reporting.**

**Migration philosophy.** Migrations are the schema's history, not a
convenience. Alembic is the *only* thing permitted to create or alter this
schema — there is no `create_all` path in application code, and reintroducing
one is how the incident above happened. The database always knows which
revision it is at; anything that leaves it unstamped is a defect.

### Migration rules — these are not preferences

- **Never edit a migration that has been released.** Reconcile forward with a
  new one. An edited migration means two databases that ran "the same" revision
  have different schemas, and nothing can tell you which is which.
- **Never call `Base.metadata.create_all` outside a test fixture.** It builds
  the schema without writing `alembic_version`, which makes the database
  permanently unmigratable — `upgrade` then replays the first revision and dies
  on `table already exists`. This is exactly what the removed fallback in
  `bootstrap.ensure_schema` did, once per boot, while logging it.
- **Never create a second declarative base.** One `Base`, in `app/db.py`.
- **Every model must be reachable from `Base.metadata`.** If Alembic cannot see
  it, its table simply never exists in production.
- **Every schema change needs a migration in the same commit.** The drift test
  fails otherwise, which is the point.
- **Never hardcode a database URL** — not in `alembic.ini`, not in a script, not
  in a test. Set `DATABASE_URL`.
- **Verify on a fresh database before merging**, not only against your own
  already-migrated one. `make migrate` on an existing database proves nothing
  about the empty case.
- **A migration must not import application models.** It runs against schemas
  from months ago; models describe today. Write the columns out literally.
- **Each revision must stand alone.** A migration that only works when run in
  the same batch as its neighbour fails on the one deployment that was
  interrupted halfway.
- **A schema that is behind must degrade, not lie.** The app starts, `/api/health`
  returns 503 and names the gap. Silent success on a broken schema is worse than
  a failed boot.

### Diagnose before you fix

There are four distinguishable states and they need **different** fixes.
Guessing between them is what turned a ten-minute problem into a wedged
database. Run this first, always:

```bash
curl -s localhost:8000/api/health | python3 -m json.tool   # state + what to run
```

Or, without a running app:

```bash
cd backend
python3 -c "
from app.db import engine
from app.migration_state import inspect_database
print(inspect_database(engine).summary)"
```

| State | What it means | The fix |
|---|---|---|
| `EMPTY` | No tables | `alembic upgrade head` |
| `UNSTAMPED` | Tables exist, no `alembic_version` — built outside Alembic | `alembic stamp head` **only if** the schema is already current; otherwise drop and migrate |
| `BEHIND` | Stamped, older than head | `alembic upgrade head` — the only case where this is the answer |
| `UNKNOWN_REV` | Stamped with a revision this code does not have | Deploy the code that owns it. **Do not upgrade** |
| `CURRENT` | At head | Look elsewhere — this is not a migration problem |

### Troubleshooting

```bash
cd backend
python3 -m alembic current        # what the database claims
python3 -m alembic heads          # what this codebase ends at
python3 -m alembic history --verbose | head -40
```

**"Pending `alembic upgrade head`" in an error message.** Do not act on it.
That sentence used to be printed by the client for *any* undescribed 500,
without checking. Get the real state from `/api/health` first.

**`table ... already exists` on upgrade.** The database is `UNSTAMPED`. Upgrading
will never work. Decide whether the schema is genuinely current: if it is,
`alembic stamp head`; if you are not sure, back up, drop, and migrate an empty
database. Do not delete individual tables and retry — that is the loop the
incident was stuck in.

**`database is locked`.** Not a migration problem. Something holds a long write
transaction — almost always a background job that flushes instead of committing.
Check `PRAGMA journal_mode` is `wal`, then look for a transaction spanning more
than a second or two of work. Raising `busy_timeout` alone only lengthens the
stall.

**Missing tables at runtime, or a bare 500 after a deploy.** `/api/health`
reports both the migration state and the specific missing columns. Usually
`BEHIND`: run the migration and restart.

**Multiple heads** (`alembic heads` prints more than one). Two branches each
added a migration. `alembic merge -m "merge" <rev1> <rev2>`, then verify on a
fresh database. Never resolve it by editing `down_revision` on a released
revision.

**Stale or unknown revision.** The database ran a migration this checkout does
not contain — usually a rollback to older code. Deploy the newer code, or
restore a database matching this one. Upgrading cannot fix it.

**Schema drift** (`test_a_fresh_database_matches_the_models_exactly` fails).
The models and the migrations disagree. See exactly how:

```bash
cd backend && rm -f /tmp/drift.db
DATABASE_URL="sqlite:////tmp/drift.db" python3 -m alembic upgrade head
python3 -c "
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine
from app.db import Base
from app.domain import models
with create_engine('sqlite:////tmp/drift.db').connect() as c:
    for d in compare_metadata(MigrationContext.configure(c), Base.metadata):
        print(d)"
```

Add a migration that reconciles it. Do not edit the released one, and do not
"fix" it by changing the model to match a wrong schema unless the schema is
what you actually want.

**Fresh database from nothing.**

```bash
cd backend
rm -f data/platform.db          # only in development
python3 -m app.bootstrap        # migrate + seed, idempotent
python3 -m pytest tests/decision_platform/test_migrations_integrity.py -q
```

---

## 5. SOLID, as checks rather than philosophy

Each principle restated as something you can actually look at. **All of them are
subject to the size floor in §7** — a 40-line helper does not need an interface,
and inventing one is itself the defect.

**S — Single responsibility.** A class with methods spanning unrelated verb
groups (parses / persists / emails) owns more than one reason to change. For
calibration: the largest class in this repo has 13 public methods and the median
is 1, so a new class above ~10 public methods, or one touching more than two of
{database, HTTP, filesystem}, deserves a second look.

```bash
python3 - <<'EOF'
import ast, pathlib
for p in pathlib.Path("backend/app").rglob("*.py"):
    try: t = ast.parse(p.read_text())
    except SyntaxError: continue
    for n in ast.walk(t):
        if isinstance(n, ast.ClassDef):
            pub = [x.name for x in n.body
                   if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and not x.name.startswith("_")]
            if len(pub) > 10:
                print(f"{p}:{n.lineno}  {n.name}  {len(pub)} public methods")
EOF
```

**O — Open/closed.** The signal here is an `if/elif` chain keyed on a type string
that grows every time a feature lands. One chain edited by three unrelated
changes wants a registry or a strategy; a chain written once and left alone is
fine. Check the churn before refactoring:

```bash
git log --oneline -15 -- backend/app/signals/   # same block, unrelated features?
```

**L — Liskov.** An override must not narrow accepted inputs, widen the exceptions
the base promised, or return `None` where the base guarantees a value. The
detectors in `signals/` share a base — new ones must satisfy the same contract,
not a convenient subset of it.

**I — Interface segregation.** If no implementer uses more than ~two-thirds of an
abstract class's methods, or a consumer calls two methods of a ten-method
protocol, it is two protocols. `ai/provider.py` is the model to follow: narrow
enough that `mock_provider` and `anthropic_provider` both implement all of it.

**D — Dependency inversion.** Here this means the deterministic layers do not
depend on the interpreted one (§1), and the AI provider is selected behind
`select_provider()` rather than imported concretely. It does **not** mean
wrapping SQLAlchemy.

---

## 6. After writing code — the gate

Deterministic tools first, judgement second. Never report "no duplication"
without a tool having actually looked.

```bash
make verify        # the whole gate, ~4 min
make verify-fast   # the edit loop, ~2.5 min — not enough to merge on
```

That is the entire list, and it is deliberately not written out here a second
time. This section used to enumerate five commands, `.github/workflows/gate.yml`
enumerated them again, and the two drifted:

- CI installed `ruff` unpinned. A newer release shipped a broader default rule
  set, so the gate reported **1314 lint errors with no code change behind them**.
  Lint ran *before* `pytest` in the same job, so the 1174-test backend suite was
  skipped entirely.
- CI never fetched pie-parser, which the backend imports in-process. All 25
  migration-integrity tests died at collection with `PIE corpus not found` — so
  the drift check this document leans on had never actually run.

Both survived **eight consecutive merges to `main`**, because a check that is
always red is a check nobody reads. `scripts/verify.sh` is now the single
definition of "verified": `make verify` runs it, CI runs it, and the Claude Code
stop-hook checks against it. Change the checks there and every caller changes
with it.

What it runs, in order: `ruff check .` (rule set in `ruff.toml`, version pinned
in `backend/requirements-dev.txt`) · the §1 layer invariants · the backend suite
in parallel · `tsc -b` and the production build · `alembic upgrade head` **on an
empty database**, then the drift test and the single-head check — first on
SQLite, then again on a disposable **PostgreSQL** (the dialect production runs;
provisioned by `scripts/pg_sandbox.sh`, skipped with a visible note where no
server binaries exist) · on that same server, the **row-level security** suite
against a role that is neither superuser nor owner (the only place the policies
are not inert) and the two **queue** suites (the only place a concurrent claim
is a real race rather than one shared connection) · and, still on that server,
the **restore drill**
(`scripts/restore_drill.py`): the backup procedure in `docs/hosting.md`
performed rather than described — seed, `pg_dump`, restore into an empty
database, then compare every row, every `Decimal` money Σ, every audit chain
under `trust/audit.verify` and every erasure receipt. It proves the procedure
round-trips this schema; it is not evidence about any particular production
backup, and the script's docstring says so at length. To run the whole backend
suite on Postgres instead of SQLite, set `PIE_TEST_DATABASE_URL` —
`docs/postgres.md` has the loop.

It runs every step and reports all failures at the end rather than stopping at
the first, so one red build tells you everything that is wrong.

**The empty-database run is not optional after a model change.** It is the check
that would have caught the incident in §4 — the schema and the models had
drifted apart in 130 places, invisibly, because nobody ran autogenerate against
a fresh database. Your own database is already migrated and can never exercise
the empty case; production only ever runs it.

Optional, if you want a real similarity scan and are willing to install it:
`npx jscpd --min-tokens 30 backend/app frontend/src`. Treat >30 duplicated
tokens in a contiguous block as a flag, not a failure — some repetition is
clearer than the abstraction that removes it.

Also optional, and deliberately not in `verify.sh`: if you added or changed a
screen, look at the tables in it.

```bash
git diff --name-only --diff-filter=d origin/main...HEAD -- 'frontend/src/**/*.tsx' \
  | xargs -r rg -n '<table' || true
```

A `<table>` is right for a fact panel and for the accessible table under a
chart, and wrong for anything whose row count is the size of the business —
which is a judgement, so this prints and you decide. It stays out of the gate
for the reason the paragraphs above give: seven of the eight raw tables left in
this codebase are correct, and a check that is usually wrong is a check people
learn to scroll past. It is here because the Quote Builder's line table stayed
hand-written through three UI passes and no tool ever mentioned it.

Then fill in §8 against the output.

---

## 7. Size floor — do not over-apply this

Over-applying SOLID to small, honestly-simple code is a defect in its own right,
and it is the failure mode this document is most likely to cause.

Skip §5 entirely for:

- Files under ~50 lines, or classes with fewer than 3 public methods.
- One-off scripts, migrations, fixtures, and `tools/`-style CLI entry points.
- Presentational React components with no branching logic.

A single implementation with no second one in sight does not need an interface.
"We might need it later" is not a variation point; a second caller is.

---

## 8. Self-review, per change

Fill this in against real tool output. Every flag gets a fix or a visible
trade-off — never a silent pass.

```markdown
## Redundancy & SOLID self-review — {files}

**Capability search run:** yes/no — searched: [keywords]
**Near-matches found:** [none | file:line …]
**Reuse rejected because:** [reason | "extended it instead"]

**Duplicate scan:** [not run | clean | N blocks] — [action]
**Invariant checks (§1):** [clean | violation + fix]
**SOLID:**
  - SRP: [within bounds | flagged: reason + action]
  - OCP: [no growing type-chain | flagged: extracted to registry]
  - LSP: [override contract holds | flagged: fixed]
  - ISP: [usage ratio ok | flagged: split]
  - DIP: [deterministic layers clean | flagged: fixed]
**Below the size floor (§7):** [n/a | yes — checks skipped deliberately]

**Verdict:** APPROVED / APPROVED WITH NOTED TRADE-OFF / NEEDS REWORK
```

Where a near-match was found and not reused, that reason belongs in the commit
message too, so the decision stays auditable instead of silent.

---

## 9. What this is not

- Not a blocking CI gate. Tests, types and the §1 invariants block; the SOLID
  heuristics advise. Promote a check to blocking only after it has run clean on
  this codebase for a while — false-positive fatigue kills the whole practice.
- Not a substitute for reading the code around what you are changing. Match its
  comment density, naming and idiom.
- Not a licence to refactor unrelated code. Fix what the change touches.
